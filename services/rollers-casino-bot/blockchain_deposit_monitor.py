"""Read-only blockchain address monitors used by the crypto deposit watcher.

NOWPayments is intentionally not used here.  It creates the payment session and
address, but this module verifies what arrived at that address independently.
Every adapter returns the same small transaction shape so the bot can perform
the address/currency/network/amount/confirmation checks in one place.

The public endpoints are configurable with ``DEPOSIT_*_API_URL`` or
``DEPOSIT_*_RPC_URL`` environment variables.  A provider outage returns an
error instead of an empty transaction list; an empty list means the address was
successfully checked and has no matching transfer yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os
from typing import Any

import requests


TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)


@dataclass(frozen=True)
class ChainTransaction:
    txid: str
    address: str
    amount: float
    coin: str
    network: str
    confirmations: int
    confirmed: bool
    usd_amount: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AddressScan:
    transactions: tuple[ChainTransaction, ...] = ()
    error: str = ""


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None or value == "":
            return default
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(_decimal(value, Decimal(str(default))))
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, str) and value.lower().startswith("0x"):
            return int(value, 16)
        return int(value)
    except (TypeError, ValueError):
        return default


def _address(value: Any) -> str:
    return str(value or "").strip()


def _same_address(left: Any, right: Any) -> bool:
    return _address(left).casefold() == _address(right).casefold()


def _get_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def _post_json(url: str, payload: dict[str, Any]) -> Any:
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def _network_key(network: str) -> str:
    return str(network or "").strip().upper().replace("-", "").replace("_", "")


def _coin_key(coin: str) -> str:
    return str(coin or "").strip().upper()


def _required_confirmations(deposit: dict[str, Any]) -> int:
    try:
        return max(1, int(deposit.get("required_confirmations") or 1))
    except (TypeError, ValueError):
        return 1


_CURRENCY_NETWORK_ALIASES: dict[str, tuple[str, str]] = {
    # NOWPayments network-specific currency codes.
    "USDTTRC20": ("USDT", "TRX"),
    "USDTBSC": ("USDT", "BSC"),
    "USDTERC20": ("USDT", "ETH"),
    "USDTPOLYGON": ("USDT", "POLYGON"),
    "USDCTRC20": ("USDC", "TRX"),
    "USDCBSC": ("USDC", "BSC"),
    "USDCPOLYGON": ("USDC", "POLYGON"),
    "USDCERC20": ("USDC", "ETH"),
    "BNBBSC": ("BNB", "BSC"),
    "AVAXC": ("AVAX", "AVAX"),
    "MATICETH": ("MATIC", "ETH"),
    "MATICPOLYGON": ("MATIC", "POLYGON"),
    "SHIBBSC": ("SHIB", "BSC"),
    "SHIBERC20": ("SHIB", "ETH"),
}


def _normalise_scan_asset(deposit: dict[str, Any]) -> tuple[str, str]:
    """Resolve a saved deposit to the adapter's canonical coin/network pair.

    Deposit records store the exact NOWPayments code because it is needed for
    provider reconciliation.  Blockchain adapters need the actual asset and
    chain instead (for example, ``usdttrc20`` is USDT on TRON).
    """
    raw_coin = _coin_key(
        deposit.get("coin") or deposit.get("crypto") or deposit.get("pay_currency")
    )
    raw_network = _network_key(deposit.get("network") or "")
    network_aliases = {
        "TRC20": "TRX",
        "BEP20": "BSC",
        "ERC20": "ETH",
        "ETHEREUM": "ETH",
        "SOLANA": "SOL",
        "POL": "POLYGON",
        "CCHAIN": "AVAX",
    }
    resolved_network = network_aliases.get(raw_network, raw_network)
    alias = _CURRENCY_NETWORK_ALIASES.get(raw_coin)
    if alias:
        coin, alias_network = alias
        return coin, resolved_network or alias_network

    network = resolved_network or raw_coin
    return raw_coin, network


def _make_tx(
    *,
    txid: Any,
    address: Any,
    amount: Any,
    coin: str,
    network: str,
    confirmations: int,
    confirmed: bool,
    usd_amount: Any = 0,
    raw: dict[str, Any] | None = None,
) -> ChainTransaction | None:
    txid = str(txid or "").strip()
    address = _address(address)
    amount = _float(amount)
    if not txid or not address or amount <= 0:
        return None
    return ChainTransaction(
        txid=txid,
        address=address,
        amount=amount,
        coin=_coin_key(coin),
        network=_network_key(network),
        confirmations=max(0, int(confirmations)),
        confirmed=bool(confirmed),
        usd_amount=max(0.0, _float(usd_amount)),
        raw=raw if isinstance(raw, dict) else {},
    )


def _scan_mempool_utxo(deposit: dict[str, Any], coin: str, network: str, base: str) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    required = _required_confirmations(deposit)
    try:
        txs = _get_json(f"{base.rstrip('/')}/api/address/{address}/txs")
        tip = _int(_get_json(f"{base.rstrip('/')}/api/blocks/tip/height"))
        result: list[ChainTransaction] = []
        for tx in txs if isinstance(txs, list) else []:
            if not isinstance(tx, dict):
                continue
            block_height = _int((tx.get("status") or {}).get("block_height"))
            confirmations = max(0, tip - block_height + 1) if block_height else 0
            confirmed = bool((tx.get("status") or {}).get("confirmed")) and confirmations >= required
            for output in tx.get("vout") or []:
                if not isinstance(output, dict):
                    continue
                output_address = (
                    (output.get("scriptpubkey_address"))
                    or ((output.get("scriptpubkey") or {}).get("address"))
                    or ""
                )
                if not _same_address(output_address, address):
                    continue
                item = _make_tx(
                    txid=tx.get("txid"),
                    address=output_address,
                    amount=_decimal(output.get("value")) / Decimal("100000000"),
                    coin=coin,
                    network=network,
                    confirmations=confirmations,
                    confirmed=confirmed,
                    raw=tx,
                )
                if item:
                    result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"{coin}/{network} explorer error: {exc}")


def _scan_dogecoin(deposit: dict[str, Any]) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    base = os.environ.get("DEPOSIT_DOGE_API_URL", "https://dogechain.info").rstrip("/")
    try:
        payload = _get_json(f"{base}/api/v1/address/transactions/{address}")
        entries = payload.get("transactions", []) if isinstance(payload, dict) else []
        result: list[ChainTransaction] = []
        for tx in entries if isinstance(entries, list) else []:
            if not isinstance(tx, dict):
                continue
            for output in tx.get("outputs") or tx.get("vout") or []:
                if not isinstance(output, dict):
                    continue
                output_address = output.get("address") or output.get("scriptPubKey", {}).get("addresses", [""])[0]
                if not _same_address(output_address, address):
                    continue
                item = _make_tx(
                    txid=tx.get("hash") or tx.get("txid"),
                    address=output_address,
                    amount=output.get("value") or output.get("amount"),
                    coin="DOGE",
                    network="DOGE",
                    confirmations=_int(tx.get("confirmations")),
                    confirmed=_int(tx.get("confirmations")) >= _required_confirmations(deposit),
                    raw=tx,
                )
                if item:
                    result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"DOGE/DOGE explorer error: {exc}")


EVM_NETWORKS = {
    "ETH": {
        "rpc": "https://ethereum-rpc.publicnode.com",
        "explorer": "https://eth.blockscout.com/api/v2",
    },
    "BSC": {
        "rpc": "https://bsc-rpc.publicnode.com",
        "explorer": "https://bscscan.com",
    },
    "POLYGON": {
        "rpc": "https://polygon-bor-rpc.publicnode.com",
        "explorer": "https://polygon.blockscout.com/api/v2",
    },
    "AVAX": {
        "rpc": "https://avalanche-c-chain-rpc.publicnode.com",
        "explorer": "https://subnets.avax.network/c-chain/api",
    },
}

EVM_TOKEN_CONTRACTS = {
    ("ETH", "USDT"): ("0xdac17f958d2ee523a2206206994597c13d831ec7", 6),
    ("ETH", "USDC"): ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6),
    ("ETH", "SHIB"): ("0x95ad61b0a150d79219dcf64e1e6cc01f0c0a3f8", 18),
    ("ETH", "LINK"): ("0x514910771af9ca656af840dff83e8264ecf986ca", 18),
    ("BSC", "USDT"): ("0x55d398326f99059ff775485246999027b3197955", 18),
    ("BSC", "USDC"): ("0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", 18),
    ("BSC", "BNB"): (None, 18),
    ("POLYGON", "MATIC"): (None, 18),
    ("POLYGON", "USDT"): ("0xc2132d05d31c914a87c6611c10748aeb04b58e8f", 6),
    ("POLYGON", "USDC"): ("0x3c499c542cef5e3811e1192ce70d8cc03d5c3359", 6),
    ("AVAX", "AVAX"): (None, 18),
}


def _evm_rpc(network: str) -> str:
    env_name = f"DEPOSIT_{network}_RPC_URL"
    return os.environ.get(env_name, EVM_NETWORKS[network]["rpc"]).rstrip("/")


def _rpc(rpc_url: str, method: str, params: list[Any]) -> Any:
    result = _post_json(
        rpc_url,
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(str(result["error"]))
    return result.get("result") if isinstance(result, dict) else None


def _scan_evm(deposit: dict[str, Any], network: str, coin: str) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    rpc_url = _evm_rpc(network)
    contract, decimals = EVM_TOKEN_CONTRACTS.get((network, coin), (None, 18))
    required = _required_confirmations(deposit)
    try:
        latest = _int(_rpc(rpc_url, "eth_blockNumber", []))
        from_block = max(0, latest - 5000)
        result: list[ChainTransaction] = []
        if contract:
            padded_to = "0x" + address[2:].lower().rjust(64, "0") if address.lower().startswith("0x") else ""
            if not padded_to:
                return AddressScan(error="invalid EVM deposit address")
            logs = _rpc(
                rpc_url,
                "eth_getLogs",
                [{
                    "fromBlock": hex(from_block),
                    "toBlock": hex(latest),
                    "address": contract,
                    "topics": [TRANSFER_TOPIC, None, padded_to],
                }],
            ) or []
            for log in logs if isinstance(logs, list) else []:
                if not isinstance(log, dict):
                    continue
                block_number = _int(log.get("blockNumber"))
                confirmations = max(0, latest - block_number + 1)
                receipt = _rpc(rpc_url, "eth_getTransactionReceipt", [log.get("transactionHash")])
                if isinstance(receipt, dict) and receipt.get("status") not in (None, "0x1"):
                    continue
                value = _int(log.get("data"))
                item = _make_tx(
                    txid=log.get("transactionHash"),
                    address=address,
                    amount=Decimal(value) / (Decimal(10) ** decimals),
                    coin=coin,
                    network=network,
                    confirmations=confirmations,
                    confirmed=confirmations >= required,
                    raw=log,
                )
                if item:
                    result.append(item)
            return AddressScan(tuple(result))

        # Native EVM transfers are indexed through Blockscout when available.
        explorer = os.environ.get(f"DEPOSIT_{network}_EXPLORER_URL", EVM_NETWORKS[network]["explorer"])
        payload = _get_json(
            f"{explorer.rstrip('/')}/addresses/{address}/transactions",
            params={"filter": "to", "page": 1},
        )
        entries = payload.get("items", []) if isinstance(payload, dict) else []
        for tx in entries if isinstance(entries, list) else []:
            if not isinstance(tx, dict):
                continue
            to = tx.get("to") or {}
            to_hash = to.get("hash") if isinstance(to, dict) else to
            if not _same_address(to_hash, address):
                continue
            block_number = _int(tx.get("block"))
            confirmations = max(0, latest - block_number + 1) if block_number else 0
            item = _make_tx(
                txid=tx.get("hash"),
                address=address,
                amount=Decimal(_int(tx.get("value"))) / (Decimal(10) ** decimals),
                coin=coin,
                network=network,
                confirmations=confirmations,
                confirmed=confirmations >= required,
                raw=tx,
            )
            if item:
                result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"{coin}/{network} RPC error: {exc}")


def _scan_tron(deposit: dict[str, Any], coin: str) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    base = os.environ.get("DEPOSIT_TRON_API_URL", "https://api.trongrid.io").rstrip("/")
    required = _required_confirmations(deposit)
    try:
        if coin in {"USDT", "USDC"}:
            payload = _get_json(
                f"{base}/v1/accounts/{address}/transactions/trc20",
                params={"only_confirmed": "false", "limit": 200},
            )
            entries = payload.get("data", []) if isinstance(payload, dict) else []
            result: list[ChainTransaction] = []
            contract_filter = str(deposit.get("token_contract") or "").casefold()
            for tx in entries if isinstance(entries, list) else []:
                if not isinstance(tx, dict) or not _same_address(tx.get("to"), address):
                    continue
                token_info = tx.get("token_info") or {}
                symbol = str(token_info.get("symbol") or "").upper()
                if symbol and symbol != coin:
                    continue
                if contract_filter and str(tx.get("token_info", {}).get("address") or "").casefold() != contract_filter:
                    continue
                decimals = _int(token_info.get("decimals"), 6)
                raw_amount = _decimal(tx.get("value"))
                block_info = _post_json(
                    f"{base}/wallet/gettransactioninfobyid",
                    {"value": tx.get("transaction_id")},
                )
                block_number = _int((block_info or {}).get("blockNumber"))
                current = _post_json(f"{base}/walletsolidity/getnowblock", {})
                current_number = _int(
                    ((current or {}).get("block_header") or {}).get("raw_data", {}).get("number")
                )
                confirmations = max(0, current_number - block_number + 1) if block_number else 0
                item = _make_tx(
                    txid=tx.get("transaction_id"),
                    address=tx.get("to"),
                    amount=raw_amount / (Decimal(10) ** decimals),
                    coin=coin,
                    network="TRX",
                    confirmations=confirmations,
                    confirmed=confirmations >= required,
                    raw=tx,
                )
                if item:
                    result.append(item)
            return AddressScan(tuple(result))

        payload = _get_json(
            f"{base}/v1/accounts/{address}/transactions",
            params={"only_confirmed": "false", "limit": 200},
        )
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        result = []
        for tx in entries if isinstance(entries, list) else []:
            contract = (tx.get("raw_data", {}).get("contract") or [{}])[0]
            value = ((contract.get("parameter") or {}).get("value") or {})
            if contract.get("type") != "TransferContract" or value.get("to_address") != address:
                continue
            item = _make_tx(
                txid=tx.get("txID"),
                address=address,
                amount=Decimal(_int(value.get("amount"))) / Decimal(1_000_000),
                coin=coin,
                network="TRX",
                confirmations=required,
                confirmed=True,
                raw=tx,
            )
            if item:
                result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"{coin}/TRX explorer error: {exc}")


def _scan_solana(deposit: dict[str, Any], coin: str) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    rpc_url = os.environ.get("DEPOSIT_SOL_RPC_URL", "https://api.mainnet-beta.solana.com")
    required = _required_confirmations(deposit)
    try:
        signatures = _rpc(rpc_url, "getSignaturesForAddress", [address, {"limit": 100}]) or []
        result: list[ChainTransaction] = []
        for sig in signatures if isinstance(signatures, list) else []:
            if not isinstance(sig, dict) or not sig.get("signature"):
                continue
            parsed = _rpc(
                rpc_url,
                "getTransaction",
                [sig["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
            if not isinstance(parsed, dict):
                continue
            meta = parsed.get("meta") or {}
            message = ((parsed.get("transaction") or {}).get("message") or {})
            account_keys = message.get("accountKeys") or []
            account_index = next(
                (
                    i for i, key in enumerate(account_keys)
                    if _same_address(key.get("pubkey") if isinstance(key, dict) else key, address)
                ),
                -1,
            )
            amount = Decimal("0")
            if account_index >= 0:
                post = _int((meta.get("postBalances") or [])[account_index] if account_index < len(meta.get("postBalances") or []) else 0)
                pre = _int((meta.get("preBalances") or [])[account_index] if account_index < len(meta.get("preBalances") or []) else 0)
                amount = Decimal(max(0, post - pre)) / Decimal(1_000_000_000)
            # SPL token transfers are represented by a balance delta owned by
            # the generated address.  The session may carry the mint from the
            # provider response; absent a mint, accept the selected SPL coin.
            if coin != "SOL":
                before = {str(x.get("accountIndex")): x for x in meta.get("preTokenBalances") or []}
                after = {str(x.get("accountIndex")): x for x in meta.get("postTokenBalances") or []}
                amount = Decimal("0")
                for index, entry in after.items():
                    if str(entry.get("owner") or "") != address:
                        continue
                    post_ui = _decimal((entry.get("uiTokenAmount") or {}).get("uiAmountString"))
                    pre_ui = _decimal((before.get(index) or {}).get("uiTokenAmount", {}).get("uiAmountString"))
                    amount += max(Decimal("0"), post_ui - pre_ui)
            if amount <= 0:
                continue
            confirmations = required if sig.get("confirmationStatus") == "finalized" else 0
            item = _make_tx(
                txid=sig["signature"],
                address=address,
                amount=amount,
                coin=coin,
                network="SOL",
                confirmations=confirmations,
                confirmed=confirmations >= required,
                raw=parsed,
            )
            if item:
                result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"{coin}/SOL RPC error: {exc}")


def _scan_xrp(deposit: dict[str, Any]) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    rpc_url = os.environ.get("DEPOSIT_XRP_RPC_URL", "https://xrplcluster.com")
    required = _required_confirmations(deposit)
    try:
        payload = _post_json(
            rpc_url,
            {"method": "account_tx", "params": [{"account": address, "limit": 200, "forward": False}]},
        )
        result: list[ChainTransaction] = []
        for row in (payload.get("result", {}).get("transactions", []) if isinstance(payload, dict) else []):
            tx = row.get("tx") if isinstance(row, dict) else {}
            if not isinstance(tx, dict) or not _same_address(tx.get("Destination"), address):
                continue
            amount = tx.get("Amount")
            if isinstance(amount, dict):
                # Issued XRP-like tokens need an explicit currency/network
                # contract to avoid crediting the wrong asset.
                selected = str(deposit.get("pay_currency") or "").upper()
                if str(amount.get("currency") or "").upper() != selected:
                    continue
                value = amount.get("value")
            else:
                value = Decimal(_int(amount)) / Decimal(1_000_000)
            validated = bool(row.get("validated"))
            item = _make_tx(
                txid=tx.get("hash"),
                address=tx.get("Destination"),
                amount=value,
                coin="XRP",
                network="XRP",
                confirmations=required if validated else 0,
                confirmed=validated,
                raw=row,
            )
            if item:
                result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"XRP/XRP RPC error: {exc}")


def _scan_xlm(deposit: dict[str, Any]) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    base = os.environ.get("DEPOSIT_XLM_API_URL", "https://horizon.stellar.org").rstrip("/")
    required = _required_confirmations(deposit)
    try:
        payload = _get_json(f"{base}/accounts/{address}/payments", params={"order": "desc", "limit": 200})
        entries = payload.get("_embedded", {}).get("records", []) if isinstance(payload, dict) else []
        result: list[ChainTransaction] = []
        for row in entries if isinstance(entries, list) else []:
            if not isinstance(row, dict) or row.get("type") not in {"payment", "path_payment_strict_receive"}:
                continue
            if not _same_address(row.get("to"), address):
                continue
            asset = str(row.get("asset_code") or "XLM").upper()
            selected = str(deposit.get("pay_currency") or "XLM").upper()
            if asset != selected and not (asset == "XLM" and selected in {"XLM", "XML"}):
                continue
            ledger = _int(row.get("ledger"))
            item = _make_tx(
                txid=row.get("transaction_hash"),
                address=row.get("to"),
                amount=row.get("amount"),
                coin="XLM",
                network="XLM",
                confirmations=required,
                confirmed=True,
                raw=row,
            )
            if item:
                result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"XLM/XLM explorer error: {exc}")


def _scan_near(deposit: dict[str, Any]) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    base = os.environ.get("DEPOSIT_NEAR_API_URL", "https://api.nearblocks.io/v1").rstrip("/")
    required = _required_confirmations(deposit)
    try:
        payload = _get_json(f"{base}/account/{address}/txns", params={"per_page": 100})
        entries = payload.get("txns", []) if isinstance(payload, dict) else []
        result: list[ChainTransaction] = []
        for row in entries if isinstance(entries, list) else []:
            if not isinstance(row, dict):
                continue
            receiver = row.get("receiver_account_id") or row.get("receiver")
            if receiver and receiver != address:
                continue
            amount = _decimal(row.get("deposit") or row.get("actions_agg", {}).get("deposit"))
            item = _make_tx(
                txid=row.get("transaction_hash") or row.get("hash"),
                address=receiver or address,
                amount=amount / Decimal(10**24),
                coin="NEAR",
                network="NEAR",
                confirmations=required,
                confirmed=True,
                raw=row,
            )
            if item:
                result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"NEAR/NEAR explorer error: {exc}")


def _scan_cosmos(deposit: dict[str, Any]) -> AddressScan:
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    base = os.environ.get("DEPOSIT_ATOM_API_URL", "https://cosmos-rest.publicnode.com").rstrip("/")
    required = _required_confirmations(deposit)
    try:
        payload = _get_json(
            f"{base}/cosmos/tx/v1beta1/txs",
            params={"events": f"transfer.recipient='{address}'", "pagination.limit": 100},
        )
        entries = payload.get("txs", []) if isinstance(payload, dict) else []
        result: list[ChainTransaction] = []
        for row in entries if isinstance(entries, list) else []:
            body = ((row.get("body") or {}).get("messages") or [{}])[0]
            if not isinstance(body, dict) or body.get("to_address") != address:
                continue
            amount = ((body.get("amount") or [{}])[0] or {}).get("amount")
            item = _make_tx(
                txid=row.get("txhash"),
                address=body.get("to_address"),
                amount=Decimal(_int(amount)) / Decimal(1_000_000),
                coin="ATOM",
                network="ATOM",
                confirmations=required,
                confirmed=True,
                raw=row,
            )
            if item:
                result.append(item)
        return AddressScan(tuple(result))
    except Exception as exc:
        return AddressScan(error=f"ATOM/ATOM explorer error: {exc}")


def scan_deposit_address(deposit: dict[str, Any]) -> AddressScan:
    """Scan one persisted deposit address without contacting NOWPayments."""
    address = _address(deposit.get("pay_address") or deposit.get("deposit_address"))
    if len(address) < 20:
        return AddressScan(error="invalid or incomplete deposit address")
    coin, network = _normalise_scan_asset(deposit)
    if coin in {"USDT", "USDC"} and network == "TRX":
        return _scan_tron(deposit, coin)
    if network == "TRX" and coin == "TRX":
        return _scan_tron(deposit, coin)
    if network == "BTC":
        return _scan_mempool_utxo(deposit, coin, network, os.environ.get("DEPOSIT_BTC_API_URL", "https://mempool.space"))
    if network == "LTC":
        return _scan_mempool_utxo(deposit, coin, network, os.environ.get("DEPOSIT_LTC_API_URL", "https://litecoinspace.org"))
    if network == "DOGE":
        return _scan_dogecoin(deposit)
    if network in EVM_NETWORKS:
        return _scan_evm(deposit, network, coin)
    if network == "SOL":
        return _scan_solana(deposit, coin)
    if network == "XRP":
        return _scan_xrp(deposit)
    if network == "XLM":
        return _scan_xlm(deposit)
    if network == "NEAR":
        return _scan_near(deposit)
    if network == "ATOM":
        return _scan_cosmos(deposit)
    return AddressScan(error=f"no independent blockchain adapter for {coin}/{network}")
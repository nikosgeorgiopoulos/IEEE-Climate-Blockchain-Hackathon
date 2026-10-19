"""Anchor Merkle roots on Polygon Amoy (testnet) via the AnchorRegistry contract."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from ..config import get_settings
from .base import AnchorResult

log = logging.getLogger(__name__)
ABI_PATH = Path(__file__).resolve().parents[2] / "contracts" / "AnchorRegistry.abi.json"
ZERO = b"\x00" * 32
MIN_TIP_GWEI = 30  # Amoy rejects priority fees below 25 gwei


def fee_params(w3) -> dict:
    """EIP-1559 fees with a tip that Amoy accepts."""
    from web3 import Web3

    base = w3.eth.get_block("latest").get("baseFeePerGas", 0) or 0
    try:
        tip = int(w3.eth.max_priority_fee)
    except Exception:
        tip = 0
    tip = max(tip, Web3.to_wei(MIN_TIP_GWEI, "gwei"))
    return {"maxPriorityFeePerGas": tip, "maxFeePerGas": 2 * base + tip}


def raw_tx(signed) -> bytes:
    """eth-account renamed rawTransaction -> raw_transaction; support both."""
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def _hex(h) -> str:
    s = h.hex() if hasattr(h, "hex") else str(h)
    return s if s.startswith("0x") else "0x" + s


class PolygonBackend:
    name = "polygon"
    explorer_label = "Polygonscan"

    def __init__(self, w3=None, contract=None, account=None):
        self._w3, self._contract, self._account = w3, contract, account
        s = get_settings()
        self.explorer = s.polygon_explorer.rstrip("/")
        self.chain_id = s.polygon_chain_id
        self.receipt_timeout = 120

    # -- lazy connection, so the app starts even when polygon isn't configured
    def _connect(self):
        if self._contract is not None:
            return self._w3, self._contract, self._account
        from eth_account import Account
        from web3 import Web3

        s = get_settings()
        if not s.anchor_contract_address:
            raise RuntimeError("ANCHOR_CONTRACT_ADDRESS is not set — run `make deploy` first")
        w3 = Web3(Web3.HTTPProvider(s.polygon_rpc_url, request_kwargs={"timeout": 30}))
        abi = json.loads(ABI_PATH.read_text())
        self._w3 = w3
        self._contract = w3.eth.contract(
            address=Web3.to_checksum_address(s.anchor_contract_address), abi=abi)
        self._account = Account.from_key(s.polygon_private_key) if s.polygon_private_key else None
        return self._w3, self._contract, self._account

    def tx_url(self, tx: str) -> str:
        return f"{self.explorer}/tx/{tx}"

    def anchor(self, root: bytes, batch_id: int) -> AnchorResult:
        w3, c, acct = self._connect()
        if acct is None:
            raise RuntimeError("POLYGON_PRIVATE_KEY is not set (use a TESTNET-ONLY key)")
        existing = bytes(c.functions.roots(batch_id).call())
        if existing != ZERO:
            if existing == root:
                return AnchorResult(status="confirmed", detail="already anchored on-chain")
            return AnchorResult(status="failed", detail=(
                f"batch id {batch_id} already holds a different root on this contract "
                "(database was reset?) — deploy a fresh contract with `make deploy`"))
        tx = c.functions.anchor(batch_id, root).build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
            "chainId": self.chain_id,
            **fee_params(w3),
        })
        signed = acct.sign_transaction(tx)
        tx_hash = _hex(w3.eth.send_raw_transaction(raw_tx(signed)))
        log.info("batch %s: sent anchor tx %s", batch_id, tx_hash)
        try:
            rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self.receipt_timeout)
        except Exception as e:  # web3.exceptions.TimeExhausted, RPC hiccups
            log.warning("batch %s: no receipt yet (%s); will refresh later", batch_id, e)
            return AnchorResult(status="submitted", tx=tx_hash, url=self.tx_url(tx_hash))
        return self._from_receipt(w3, rcpt, tx_hash)

    def _from_receipt(self, w3, rcpt, tx_hash: str) -> AnchorResult:
        if rcpt["status"] != 1:
            return AnchorResult(status="failed", tx=tx_hash, url=self.tx_url(tx_hash),
                                detail="transaction reverted")
        block = rcpt["blockNumber"]
        try:
            ts = w3.eth.get_block(block)["timestamp"]
            at = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return AnchorResult(status="confirmed", tx=tx_hash, url=self.tx_url(tx_hash),
                            block=block, anchored_at=at)

    def refresh(self, batch) -> AnchorResult:
        w3, c, _ = self._connect()
        if batch["anchor_status"] == "submitted" and batch["anchor_tx"]:
            try:
                rcpt = w3.eth.get_transaction_receipt(batch["anchor_tx"])
            except Exception:  # TransactionNotFound: still in the mempool
                return AnchorResult(status="submitted")
            if rcpt is None:
                return AnchorResult(status="submitted")
            return self._from_receipt(w3, rcpt, batch["anchor_tx"])
        return self.anchor(bytes.fromhex(batch["merkle_root_hex"]), batch["id"])

    def lookup(self, batch) -> bytes | None:
        _, c, _ = self._connect()
        root = bytes(c.functions.roots(batch["id"]).call())
        return None if root == ZERO else root

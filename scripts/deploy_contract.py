#!/usr/bin/env python3
"""Compile contracts/AnchorRegistry.sol (solc 0.8.24 via py-solc-x) and deploy it to Amoy.

Needs POLYGON_PRIVATE_KEY in .env — a TESTNET-ONLY key funded from a faucet.
Prints the contract address to paste into .env as ANCHOR_CONTRACT_ADDRESS.
"""

import argparse
import json
import sys

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

from proofofair.config import get_settings

SOLC_VERSION = "0.8.24"
SOURCE = ROOT / "contracts" / "AnchorRegistry.sol"
ABI_OUT = ROOT / "contracts" / "AnchorRegistry.abi.json"


def compile_contract() -> tuple[list, str]:
    import solcx

    if SOLC_VERSION not in [str(v) for v in solcx.get_installed_solc_versions()]:
        print(f"Installing solc {SOLC_VERSION}…", flush=True)
        solcx.install_solc(SOLC_VERSION)
    out = solcx.compile_files(
        [str(SOURCE)], output_values=["abi", "bin"], solc_version=SOLC_VERSION,
        optimize=True, optimize_runs=200,
    )
    key = next(k for k in out if k.endswith(":AnchorRegistry"))
    return out[key]["abi"], out[key]["bin"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compile-only", action="store_true")
    args = ap.parse_args()

    abi, bytecode = compile_contract()
    ABI_OUT.write_text(json.dumps(abi, indent=2) + "\n")
    print(f"Compiled AnchorRegistry ({len(bytecode) // 2} bytes), ABI -> {ABI_OUT}")
    if args.compile_only:
        return

    from eth_account import Account
    from web3 import Web3

    s = get_settings()
    if not s.polygon_private_key:
        sys.exit("POLYGON_PRIVATE_KEY is empty. Put a TESTNET-ONLY key in .env first.")
    w3 = Web3(Web3.HTTPProvider(s.polygon_rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        sys.exit(f"Cannot reach {s.polygon_rpc_url}")
    if w3.eth.chain_id != s.polygon_chain_id:
        sys.exit(f"RPC is chain {w3.eth.chain_id}, expected {s.polygon_chain_id} (Amoy). Refusing.")
    acct = Account.from_key(s.polygon_private_key)
    bal = w3.eth.get_balance(acct.address)
    print(f"Deployer {acct.address}, balance {w3.from_wei(bal, 'ether')} POL")
    if bal == 0:
        sys.exit("Balance is 0 — get test POL from https://faucet.polygon.technology first.")

    from proofofair.anchor.polygon import fee_params, raw_tx

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor().build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": s.polygon_chain_id,
        **fee_params(w3),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(raw_tx(signed))
    print(f"Deploy tx {s.polygon_explorer}/tx/0x{tx_hash.hex().removeprefix('0x')}  (waiting…)")
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if rcpt.status != 1:
        sys.exit("Deployment reverted.")
    print(f"\nAnchorRegistry deployed at {rcpt.contractAddress} (block {rcpt.blockNumber})")
    print(f"{s.polygon_explorer}/address/{rcpt.contractAddress}\n")
    print("Add to .env:")
    print(f"  ANCHOR_CONTRACT_ADDRESS={rcpt.contractAddress}")
    print("  ANCHOR_BACKEND=polygon")


if __name__ == "__main__":
    main()

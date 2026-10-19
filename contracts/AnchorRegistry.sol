// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title ProofOfAir anchor registry
/// @notice Stores one Merkle root per batch of signed sensor readings. Write-once.
contract AnchorRegistry {
    address public immutable owner;
    mapping(uint256 => bytes32) public roots;

    event Anchored(uint256 indexed batchId, bytes32 root, uint256 timestamp);

    constructor() {
        owner = msg.sender;
    }

    function anchor(uint256 batchId, bytes32 root) external {
        require(msg.sender == owner, "not owner");
        require(roots[batchId] == bytes32(0), "exists");
        roots[batchId] = root;
        emit Anchored(batchId, root, block.timestamp);
    }
}

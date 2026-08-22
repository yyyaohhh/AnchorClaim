// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title MockUSDC
/// @notice A mintable 6-decimal ERC20 standing in for USDC on testnets, where real
///         faucet USDC is issued in amounts far too small to demo demurrage claims
///         in the tens or hundreds of thousands of dollars. Owner-only mint lets the
///         demo fund voyages at whatever scale the sample data calls for.
contract MockUSDC is ERC20, Ownable {
    constructor() ERC20("Mock USD Coin", "mUSDC") Ownable(msg.sender) {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }
}

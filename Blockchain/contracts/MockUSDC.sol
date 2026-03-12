// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title  MockUSDC — Dev/Test ERC-20 Stablecoin
 * @notice Mintable ERC-20 with 6 decimals (like real USDC).
 *         Used as the deposit currency for CollateralSC in dev/test.
 *         In production, replace with the real USDC contract address.
 */
contract MockUSDC is ERC20, Ownable {
    uint8 private constant _DECIMALS = 6;

    constructor(address initialOwner)
        ERC20("Mock USDC", "USDC")
        Ownable(initialOwner)
    {
        // Mint 1 000 000 USDC to deployer for testing
        _mint(initialOwner, 1_000_000 * 10 ** _DECIMALS);
    }

    function decimals() public pure override returns (uint8) {
        return _DECIMALS;
    }

    /**
     * @notice Mint tokens to any address (owner only, dev/test utility).
     */
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }
}

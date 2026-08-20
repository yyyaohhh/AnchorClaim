// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title AnchorClaimEscrow
/// @notice Escrow and automated settlement for maritime demurrage.
///         The charterer and shipowner deposit freight plus a security deposit into
///         this escrow vault. Off-chain, the Voyage Sentinel agent audits the charter
///         party against port/AIS records, computes the demurrage, and produces an
///         attestation. An authorized attestor (the agent's signing address) submits
///         it, and the contract automatically deducts the penalty from the deposit to
///         the owner and releases the remaining balance to the charterer.
contract AnchorClaimEscrow is ReentrancyGuard, Ownable {
    IERC20 public immutable settlementToken; // USDC (6 decimals)

    enum Status { None, Funded, Settled, Refunded }

    struct Voyage {
        address charterer;    // charterer (posts freight + deposit; penalized on overrun)
        address shipowner;    // shipowner (receives demurrage on overrun)
        uint256 freight;      // freight (always paid to the owner)
        uint256 deposit;      // security deposit (demurrage is deducted from this)
        uint64  laytimeHours; // contractual free laytime (e.g. 48 hours)
        Status  status;
    }

    struct Attestation {
        uint64  actualHours;  // actual in-port hours (verified from SoF/AIS)
        uint256 demurrage;    // computed demurrage
        bytes32 evidenceHash; // hash of the audit evidence (contract + SoF + AIS)
    }

    address public attestor; // authorized signing address of the Voyage Sentinel agent
    mapping(bytes32 => Voyage) public voyages;
    mapping(bytes32 => Attestation) public attestations;

    event VoyageFunded(bytes32 indexed id, address charterer, address shipowner, uint256 freight, uint256 deposit, uint64 laytimeHours);
    event VoyageSettled(bytes32 indexed id, uint256 demurrage, uint256 refundToCharterer, bytes32 evidenceHash);
    event VoyageRefunded(bytes32 indexed id, uint256 amount);

    constructor(address _token, address _attestor) Ownable(msg.sender) {
        settlementToken = IERC20(_token);
        attestor = _attestor;
    }

    /// @notice Fund a voyage: the charterer deposits freight + security deposit to open
    ///         an escrow. Requires a prior approve().
    function fund(bytes32 id, address shipowner, uint256 freight, uint256 deposit, uint64 laytimeHours)
        external nonReentrant
    {
        require(voyages[id].status == Status.None, "voyage exists");
        require(shipowner != address(0) && laytimeHours > 0, "bad params");
        uint256 total = freight + deposit;
        require(total > 0, "zero amount");

        voyages[id] = Voyage(msg.sender, shipowner, freight, deposit, laytimeHours, Status.Funded);
        require(settlementToken.transferFrom(msg.sender, address(this), total), "transfer failed");
        emit VoyageFunded(id, msg.sender, shipowner, freight, deposit, laytimeHours);
    }

    /// @notice Settle a voyage: only the attestor may submit the audit result. The
    ///         contract pays the owner the full freight plus demurrage (capped at the
    ///         deposit) and refunds the remaining deposit to the charterer.
    function settle(bytes32 id, uint64 actualHours, uint256 demurrage, bytes32 evidenceHash)
        external nonReentrant
    {
        require(msg.sender == attestor, "not attestor");
        Voyage storage v = voyages[id];
        require(v.status == Status.Funded, "not funded");

        uint256 penalty = demurrage > v.deposit ? v.deposit : demurrage; // capped at the deposit
        uint256 refund = v.deposit - penalty;

        v.status = Status.Settled;
        attestations[id] = Attestation(actualHours, demurrage, evidenceHash);

        // owner receives: freight + deducted demurrage
        require(settlementToken.transfer(v.shipowner, v.freight + penalty), "owner pay failed");
        // charterer receives: remaining deposit
        if (refund > 0) require(settlementToken.transfer(v.charterer, refund), "refund failed");

        emit VoyageSettled(id, penalty, refund, evidenceHash);
    }

    function setAttestor(address _attestor) external onlyOwner {
        attestor = _attestor;
    }
}

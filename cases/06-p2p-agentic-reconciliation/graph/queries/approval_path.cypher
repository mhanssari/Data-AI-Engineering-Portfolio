// The approval path of one requisition compared with each approver's limit on the approval date (not their limit today).
// Parameters: $source_system, $pr_number. Money is held as strings in the graph, so toFloat is for this display only:
// the authoritative check, with currency conversion, is the ApprovalLimitExceeded control in SQL.
MATCH (pr:PurchaseRequisition {sourceSystem: $source_system, documentNumber: $pr_number})-[:REQUESTED_BY]->(requester:Employee)
OPTIONAL MATCH (pr)-[:HAS_APPROVAL]->(approval:Approval)-[:APPROVED_BY]->(approver:Employee)
RETURN pr.documentNumber AS pr_number,
       pr.amount AS amount,
       pr.currencyCode AS currency,
       requester.partyName AS requester,
       approval.approvalStep AS step,
       approver.partyName AS approver,
       approval.limitAtApproval AS limit_at_approval,
       approval.currencyCode AS limit_currency,
       approver.approvalLimit AS limit_today,
       requester = approver AS self_approved,
       toFloat(pr.amount) > toFloat(approval.limitAtApproval) AS above_limit_unconverted
ORDER BY step

# ProfitShield — NOVA 2026 speaker notes

Eight pitch slides + two optional appendix slides. Aim for about four minutes, including a 45-second live demo.

## 1. Good food. Stronger margins.

A restaurant owner should not need five tabs to understand what the business keeps. ProfitShield brings payouts, sales and supplier evidence into one workflow, then helps the owner inspect a finding and decide what to do next.

## 2. Five reports. One unanswered question.

The DoorDash back-house prompt is about records scattered across sales reports, invoices, receipts and payouts. Owners spend time combining them before they can even ask what changed. Our focus is the financial decision: show the baseline, show the evidence, and keep the next action under the owner’s control.

## 3. One workspace. A decision you can inspect.

Switch to the demo for about 45 seconds. Load the example restaurant. Show the financial summary, open the commission finding, then click Explain finding. Point out that the source row and the same baseline appear together. If time is short, use this actual screenshot. Do not enable external services during judging.

## 4. Start with a number you can defend.

The golden dataset has ten thousand dollars in gross sales. Refunds and restaurant-funded promotions bring net sales to nine thousand two hundred. Commissions, food and other variable costs leave four thousand seven hundred in contribution margin. That is 51.09 percent of net sales. We do not call that accounting profit: operating expenses are incomplete. Platform-funded promotions are informational, and commission discrepancies are not deducted twice.

## 5. A $100 question. With the receipts.

Here is the evidence-first behavior. A four-thousand-dollar DoorDash row records nine hundred in commission. At the supplied twenty-percent contract rate, the expected amount is eight hundred. The one-hundred-dollar difference is conditional exposure, not money recovered. The owner can prepare a draft with that row and assumption. And two invoice copies produce an inquiry, not an invented duplicate-payment claim.

## 6. +$427.50—if the assumptions hold.

The owner can test a five-percent menu price increase against exactly the same records. With demand and quantities held constant, the engine returns five thousand one hundred twenty-seven dollars and fifty cents in contribution margin—a four-hundred-twenty-seven-dollar-and-fifty-cent improvement. Refunds and promotions stay fixed in dollars. This is conditional arithmetic, not a forecast or a live menu change. Approval is explicit and the result is labeled Simulated.

## 7. Interpretation. Arithmetic. Approval.

Our product direction puts AI around interpretation, code around money, and the owner around decisions. Financial arithmetic runs in Python Decimal and integer cents. The connected demo uses a clearly labeled deterministic Guru; it does not pretend to be a live language model. Optional AI mapping, receipt extraction and Q&A exist in the historical path and are privacy-gated. The useful result is a repeatable workflow with evidence and review controls.

## 8. See the leak. Understand the tradeoff. Own the decision.

ProfitShield connects the records behind the restaurant to a decision the owner can inspect. It fits the DoorDash back-house challenge by reducing the manual work between scattered reports, financial understanding, and a reviewed next action. Our next step is a pilot workflow with reviewed contracts and complete cost records—not claims of recovery or autonomous production changes. Good food. Stronger margins.

## 9. Two experiences. One financial core.

The code contains two experiences. The connected path uses normalized financial records, prototype workflow tables and the canonical cents engine. The historical path uses platform imports, ingredient recipes and an adapter with explicit estimation assumptions. Both are served by the Python server and stored locally in SQLite. Reviewers should use the tested UI branch, not assume development and main contain the same application.

## 10. What is real—and what comes next.

Working means the local synthetic workflow is implemented and tested. It does not mean money was recovered, production menus changed, external AI ran, or multiple locations are isolated. The next work is complete cost inputs and payment reconciliation, tenant isolation, privacy-reviewed AI, and owner validation. Sources: repository at github.com/bhuvanakanakam/profitshield; NOVA context and palette from nova.scottylabs.org; DoorDash track wording from the participant-provided brief. Product screenshot from feat/uiux-latest at 7c5b6c6.

## Live-demo fallback and sequence

Open the tested `feat/uiux-latest` branch. Sign in, load the example restaurant,
show $9,200 net / $4,700 contribution margin / 51.09%, open the commission row,
click Explain finding, calculate the 5% scenario, then review simulated approval.
Prepare the $100 conditional commission draft if time permits.
If the network or app fails, slide 3 contains an actual screenshot and slides
4–6 contain the exact synthetic walkthrough. Do not invent a live AI result.

## Claims to avoid

- Do not use the old “sales up 12%, profit down $574” story. The unsupported
  duplicate-payment deduction was removed; the corrected historical estimate
  change is +$164.03, with assumptions.
- $427.50 is a conditional result for the reference dataset, not realized savings,
  an annual number, or a demand forecast.
- $100 is conditional exposure, not a recovered payment.
- The connected demo uses a deterministic fallback. Do not call it live LLM reasoning.
- Do not promise production DoorDash API integration, automatic dispute submission,
  production Square changes, or completed multi-location isolation.

## Sources

- Theme: participant-provided DoorDash Back House prize-track brief.
- Event context and palette: https://nova.scottylabs.org/ (accessed November 7, 2026).
- Project: https://github.com/bhuvanakanakam/profitshield
- Demo/UI commit: https://github.com/bhuvanakanakam/profitshield/tree/7c5b6c6
- Calculations: financial_engine.py, demo_data/golden.csv, tests/test_prototype.py.

This is a participant deck, not an official NOVA or DoorDash template or endorsement.

# Detailed 80/20 and QUAD analysis

Compatible sales workbooks now generate complete revenue, gross-profit and transaction-loss rankings for customers and products, comparative-period changes, profitability QUADs, and a customer/product revenue matrix. Optional Customer Name and Product Name columns provide display labels. Existing required columns and authentication remain unchanged.

The revenue 80 group contains the smallest ranked set reaching at least 80% of positive group net sales, including the crossing group. This is not a fixed 20% of entities. Ties are broken by identifier. The separate top-20% statistic rounds entity count upward. Negative contributions are disclosed and excluded from positive concentration denominators. Gross-loss concentration sums loss-making transactions even within net-profitable groups.

Profitability QUADs use membership of the revenue 80 group and gross margin relative to the current weighted business margin: Q1 high/high, Q2 high/low, Q3 low/high, Q4 low/low. Margin equality counts as high. Zero/negative group net sales and unavailable business margins are unclassified Review groups.

The separate customer/product matrix uses revenue groups on both axes: 80/80, 80/20, 20/80 and 20/20 (customer first). Every transaction, including credits and zero-price rows, remains in one cell. Gross profit, sales, transaction counts and gross loss reconcile to the current report. These are review priorities, not verified savings or EBITDA calculations.

New saved records retain the complete calculated report and render the QUAD detail. Old saved records remain readable and instruct consultants to download and re-upload the original to obtain new calculations. JSON exports contain all rankings, definitions and pair-level matrix data under `detailed_80_20`.

Verification: unit tests cover the crossing boundary, ties, top-20% rounding, margin equality, empty/negative periods, returns and zero-price losses, reconciliation, prior-only groups, cutoff dates, optional names, HTML escaping and saved rendering. The manufacturing fixture as of 2026-09-30 reconciles to sales 36,162,705.30, gross profit 7,257,892.80 and transaction gross loss 353,332.00.

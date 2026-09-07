"""Pantry pipeline: Gmail receipts -> parsed items -> stock and restock advice.

Split the way the data flows, so a failure is easy to place:
  gmail_tool  fetch mail over IMAP (read-only)
  receipts    two OFD e-receipt templates -> structured items
  analyze     purchase cadence per product -> consumption rate
  shelf_life  keyword -> category -> how long it keeps (a guess, editable)
  inventory   stock, spoilage, and a deliberately quiet restock proposal
"""

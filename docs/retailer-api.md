# Retailer API

How a store plugs Rechoice into its own return flow. Written 28 October 2026.

1. **When an order ships,** the store registers it and gets a return link.
2. **The store puts the link** on its order page and in the delivery email, as the "Return or exchange" button. `/store` shows what that looks like.
3. **The shopper opens the link,** picks the item, says what's wrong, compares alternatives on a person and confirms an exchange request.
4. **The store reads the requests back** and its own staff act on them in the store's systems. Rechoice never changes an order, stock or money.

The API is off unless `RETAILER_API_KEY` is set (at least 32 characters). Send it as a bearer token. The demo has one store, so the key acts for the seeded catalog. Requests are pulled: Rechoice makes no outbound calls to a store, and a real integration would add signed webhooks.

## Register an order

```bash
curl -X POST https://<host>/api/retailer/orders \
  -H "Authorization: Bearer $RETAILER_API_KEY" -H "Content-Type: application/json" \
  -d '{"order_number": "DS-20481", "lines": [
        {"sku": "ORIGINAL", "paid_minor": 4000},
        {"sku": "LIP-VELVET-230", "paid_minor": 1800, "opened": true}]}'
```

- `order_number`: 3 to 40 capital letters, digits and dashes, unique per store.
- `lines`: 1 to 10 items, each a SKU from the store's catalog, the price paid in minor units, and whether it was opened. The store's current return policy applies; the credit is the price paid.

Returns `201` with `return_url` (the shopper's link), `store_page_url` and a `line_token` per line. Errors: `401` without the key, `404` when the API is off, `409` for an order number already registered, `422` for an unknown SKU or invalid input, `429` past 120 calls an hour from one address.

## Read exchange requests

```bash
curl "https://<host>/api/retailer/requests?since=2026-10-28T00:00:00Z" -H "Authorization: Bearer $RETAILER_API_KEY"
curl "https://<host>/api/retailer/requests/<request_id>" -H "Authorization: Bearer $RETAILER_API_KEY"
```

Each request has its `state` (`DRAFT`, `NEEDS_CLARIFICATION`, `REVIEWED` or `DECLINED`), the order number, the item returned and the replacement with their SKUs, whether it is another size or a different item, and the money in minor units: credit, replacement price, fee and top-up, with the times the shopper confirmed and staff reviewed it. Up to 100 per call, oldest first; page with `since`. The shopper's notes, photos and previews are never included.

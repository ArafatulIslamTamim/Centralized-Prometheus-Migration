# Latest build: matching range blocks only

This version keeps the first-style GUI but changes the range workflow to match the manual migration method.

## Key behavior

- The snapshot may contain many blocks, but transfer sends only matching blocks.
- Default matching rule is overlap:

```bash
block.maxTime > START_MS && block.minTime < END_MS
```

- This is the same rule used in the previous manual commands.
- It does not split Prometheus blocks; it copies whole matching block folders.
- The plan step uses the same matching rule as transfer, so the copied/staged blocks and planned blocks stay consistent.
- Target `/data` is not changed during snapshot, transfer, scan, or plan.
- Target `/data` changes only after Execute.
- Replacement mode moves overlapping target blocks to backup first and requires exact uppercase `YES`.

## Recommended setting

Use **Overlap requested range (recommended)**.

Use **Inside range only (advanced)** only when you intentionally want to exclude edge blocks that extend outside the requested time range.

# frodo sessions of Part B (phase 6): HF1, SMT, reproducibility, telemetry, clocks

## HF1 (non-polling host wait)

callback / xrt steady energy per option, socket boundary: median 1.0025, range 0.9952-1.0078; <= 0.90 at 0 of 36 settings. **HF1 fails.**

## SMT (24 vs 48 threads, registered choice from session 1)

- avx512: 48 threads chosen at 30 of 30 keys; e48/e24 session 1 median 0.708 (0.647-0.762)
- native: 48 threads chosen at 30 of 30 keys; e48/e24 session 1 median 0.881 (0.706-0.963)
- session 2 agrees with the choice at 60 of 60 keys

## Reproducibility

- session 1 vs 2, energy per path-step at the socket boundary, all 316 groups: median |diff| 0.14%, max 113.5%, 16 groups over 5%
- card (--wait xrt) phase 6 vs Part A, steady groups: median 1.0010 (0.9918-1.0062); short single runs up to 1.94x (see the host timing check)

## Telemetry check (exploratory; card --wait xrt, steady state; median of the sessions)

| Kernel | xrt.ini | card W | host socket W | server W | ms/run |
|---|---|---|---|---|---|
| I ft M1280 | registered | 32.8 | 49.0 | 93.2 | 795.5 |
| I ft M1280 | tel200 | 32.7 | 51.5 | 97.0 | 795.6 |
| I ft M1280 | teloff | 32.8 | 41.8 | 76.0 | 795.6 |
| P1 qe M1 | registered | 29.0 | 49.2 | 93.5 | 2046.3 |
| P1 qe M1 | tel200 | 28.7 | 49.8 | 94.1 | 2046.3 |
| P1 qe M1 | teloff | 28.7 | 41.4 | 75.4 | 2046.4 |

## Clocks and wall power

- session 1: socket 0 busy clock under load (busy > 50%) median 2496 MHz (2275-2608, 1075 intervals); wall power median 250 W (192-431)
- session 2: socket 0 busy clock under load (busy > 50%) median 2495 MHz (2293-2602, 1084 intervals); wall power median 267 W (198-431)

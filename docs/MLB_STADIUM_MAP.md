# MLB Retrosheet Stadium Map

Use `config/mlb_stadium_map.template.csv` as the starting point for a reviewed
stadium map used by MLB weather collection. The repository template is
intentionally header-only: coordinates, timezones, and elevations must be
reviewed before use and must not be inferred from a team name.

## Columns

| Column | Requirement |
| --- | --- |
| `park_id` | Required value. Retrosheet park ID from the supplied game log. IDs are matched case-insensitively and must be unique and non-blank. |
| `stadium_name` | Required column. Human-readable reviewed stadium name; a blank value is accepted by the collector and falls back to the park ID. |
| `latitude` | Required for provider lookup. Decimal degrees from `-90` through `90`; missing or invalid values produce an `invalid_coordinates` diagnostic without a provider call. |
| `longitude` | Required for provider lookup. Decimal degrees from `-180` through `180`; missing or invalid values produce an `invalid_coordinates` diagnostic without a provider call. |
| `timezone` | Required for provider lookup. Use an IANA timezone such as `America/New_York`; missing or invalid values produce a `timezone_error` diagnostic without a provider call. |
| `elevation_m` | Required column. Optional numeric elevation in metres; leave blank when it has not been reviewed. |
| `roof_type` | Optional column. Allowed values are `open`, `retractable`, `fixed_roof`, `dome`, and `unknown`. Blank values default to `unknown`; fixed-roof and dome games are diagnosed as indoor and are not sent to Meteostat. |

Save reviewed maps outside raw collection-output directories. Do not place raw
weather responses or generated collection artifacts in this template.

## Validate before collection

The validator reads local files only. It accepts both Retrosheet headerless
`glYYYY` game logs and headered game-info CSVs supported by the weather
collector.

```powershell
py scripts/mlb_validate_stadium_map.py --stadium-map C:\approved-inputs\retrosheet_stadiums.csv --game-log C:\approved-inputs\gl2025.txt
```

A valid map exits with status `0`. Validation errors are written to stderr and
exit with status `2`. The validator does not fetch data or write output files.

"""Which columns each stage needs, adds and drops in contributions_cleaned.csv.

Stages run in this order and each rewrites the cleaned CSV:

    clean.py -> geocode.py -> resolve.py --apply -> geocode.py --employer-only
    (which ends by building employer_locations.csv)

A stage checks the file it reads (check_input) and the frame it is about to
write (check_output); the loader accepts only LOADED_COLUMNS, exactly.
"""
from dataclasses import dataclass

# clean.py writes exactly these, in this order
CLEAN_COLUMNS = (
    'sub_id', 'transaction_id', 'two_year_transaction_period', 'recipient_committee',
    'entity_type',
    'contributor_name', 'contributor_first_name', 'contributor_last_name',
    'contributor_street_1', 'contributor_street_2',
    'contributor_city', 'contributor_state', 'contributor_zip',
    'contributor_employer', 'contributor_occupation', 'occupation_category',
    'contribution_receipt_date', 'contribution_receipt_amount',
    'previous_employer', 'identity_status', 'donor_key', 'employment_source',
)
DONOR_COORDINATES = ('latitude', 'longitude')
EMPLOYER_ADDRESS = ('employer_address', 'employer_city', 'employer_state', 'employer_zip')
EMPLOYER_COORDINATES = ('employer_latitude', 'employer_longitude')
# how resolve found each employer address; geocode --employer-only reads it, then drops it
RESOLVE_WORKING = ('resolve_method', 'resolve_confidence')

# what the loader reads: the file after the last stage
LOADED_COLUMNS = CLEAN_COLUMNS + DONOR_COORDINATES + ('employer_status',)


class ContractError(ValueError):
    """A stage read or was about to write columns its contract does not allow."""


@dataclass(frozen=True)
class Stage:
    command: str
    needs: tuple[str, ...] = ()
    adds: tuple[str, ...] = ()
    drops: tuple[str, ...] = ()


STAGES = {
    'clean': Stage('clean.py', adds=CLEAN_COLUMNS),
    'geocode': Stage('geocode.py', needs=CLEAN_COLUMNS, adds=DONOR_COORDINATES),
    'resolve': Stage(
        'resolve.py --apply', needs=CLEAN_COLUMNS,
        adds=EMPLOYER_ADDRESS + ('employer_status',) + RESOLVE_WORKING,
    ),
    'employer_geocode': Stage(
        'geocode.py --employer-only',
        needs=CLEAN_COLUMNS + EMPLOYER_ADDRESS + ('employer_status',) + RESOLVE_WORKING,
        adds=EMPLOYER_COORDINATES, drops=RESOLVE_WORKING,
    ),
    'employers': Stage(
        'geocode.py --employer-only (employer locations)',
        needs=CLEAN_COLUMNS + EMPLOYER_ADDRESS + EMPLOYER_COORDINATES + ('employer_status',),
        drops=EMPLOYER_ADDRESS + EMPLOYER_COORDINATES,
    ),
}


# the stage that first adds a column, for the "run X first" hint
def _added_by(column: str) -> str:
    return next((stage.command for stage in STAGES.values() if column in stage.adds), 'clean.py')


# raise unless the file a stage reads has every column it needs
def check_input(stage_name: str, columns) -> None:
    stage = STAGES[stage_name]
    missing = [column for column in stage.needs if column not in set(columns)]
    if missing:
        first = _added_by(missing[0])
        raise ContractError(
            f"{stage.command} needs columns {', '.join(missing)}; run {first} first"
        )


# raise unless a stage writes its input columns plus adds, minus drops
def check_output(stage_name: str, input_columns, output_columns) -> None:
    stage = STAGES[stage_name]
    expected = (set(input_columns) | set(stage.adds)) - set(stage.drops)
    written = list(output_columns)
    problems = []
    duplicated = sorted({column for column in written if written.count(column) > 1})
    if duplicated:
        problems.append(f"duplicated {', '.join(duplicated)}")
    extra = sorted(set(written) - expected)
    if extra:
        problems.append(f"unexpected {', '.join(extra)}")
    missing = sorted(expected - set(written))
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    working = sorted(column for column in written if column.startswith('_'))
    if working:
        problems.append(f"working columns {', '.join(working)}")
    if problems:
        raise ContractError(f"{stage.command} would write a broken file: " + '; '.join(problems))


# raise unless the file holds exactly the columns the loader reads
def check_loadable(columns) -> None:
    columns = list(columns)
    missing = [column for column in LOADED_COLUMNS if column not in columns]
    extra = [column for column in columns if column not in LOADED_COLUMNS]
    if missing or extra:
        hint = f"; run {_added_by(missing[0])}" if missing else ""
        detail = '; '.join(filter(None, (
            f"missing {', '.join(missing)}" if missing else '',
            f"unexpected {', '.join(extra)}" if extra else '',
        )))
        raise ContractError(f"contributions_cleaned.csv is not the finished file: {detail}{hint}")

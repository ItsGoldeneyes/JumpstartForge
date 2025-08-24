import argparse
import os
import psycopg2
from dotenv import load_dotenv
import io
import uuid
from datetime import datetime

from tools.deck_tools import *
# from tools.game_tools import *
from tools.database_tools import conn, cur
import pandas as pd

"""
Add games to database queue for workers to run
"""

# Set up commandline argument parser
parser = argparse.ArgumentParser(description="Add game to database queue for workers to run",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("-d", "--decks", action="store", help="comma separated list of deck names", default='all')
parser.add_argument("-n", "--games", action="store", help="number of games to run (per combination if tournament)", default='1')
parser.add_argument("-f", "--format", action="store", help="game format (constructed, commander, jumpstart)", default='constructed')
parser.add_argument("-t", "--tournament", action="store", help="tournament mode (single, roundrobin)", default='single')
parser.add_argument("-p", "--print_decks", action="store_true", help="print all decks for format, then quit")
args = vars(parser.parse_args())


# Ensure all variables have valid values
try:
    args['games'] = int(args['games'])
except (ValueError, TypeError):
    raise ValueError("The value for --games must be an integer.")
assert isinstance(args['games'], int), 'failed to convert "games" to int'

valid_formats = ['constructed', 'commander', 'jumpstart']
if args['format'] not in valid_formats:
    raise ValueError(f"Invalid format '{args['format']}'. Valid options are: {', '.join(valid_formats)}.")

valid_tournaments = ['single', 'roundrobin']
if args['tournament'] not in valid_tournaments:
    raise ValueError(f"Invalid tournament '{args['tournament']}'. Valid options are: {', '.join(valid_tournaments)}.")

# Retrieve unique deck names from database for the specified format
cur.execute(
    """SELECT DISTINCT deck_name
    FROM decks
    WHERE format = %s
    AND uploaded_on = (
        SELECT MAX(uploaded_on)
        FROM decks
        WHERE format = %s
    )
    ORDER BY deck_name ASC;""",
    (args['format'],args['format'])
)
deck_names = [row[0] for row in cur.fetchall()]

# Check decks against deck_names
if args['decks'] == 'all':
    selected_decks = deck_names
else:
    selected_decks = [d.strip() for d in args['decks'].split(',')]
    missing_decks = set(selected_decks) - set(deck_names)
    if missing_decks:
        raise ValueError(f"Deck(s) not found: {', '.join(missing_decks)}, run -p without -d to see all valid decks for format -f")

# Print decks if argument is true
if args["print_decks"] == True:
    print(selected_decks)
    cur.close()
    conn.close()
    quit()


# Create all combinations of decks
if args['format'] == 'commander':
    player_count = 4
else:
    player_count = 2

deck_combinations = itertools.combinations(selected_decks, player_count)
# Convert combinations to list of dicts for DataFrame
combo_dicts = []
for combo in deck_combinations:
    row = {}
    for i in range(player_count):
        row[f'deck{i+1}_name'] = combo[i]
    # Fill remaining with None if less than 4 players
    for i in range(player_count, 4):
        row[f'deck{i+1}_name'] = None
    combo_dicts.append(row)

games_df = pd.DataFrame(combo_dicts)

games_df.insert(0, 'primary_key', [str(uuid.uuid4()) for _ in range(len(games_df))])
games_df['job_id'] = str(uuid.uuid4())
games_df['game_count'] = args['games']
games_df['deck1_wins'] = [0] * len(games_df)
games_df['deck2_wins'] = [0] * len(games_df)
games_df['deck3_wins'] = [0] * len(games_df)
games_df['deck4_wins'] = [0] * len(games_df)
games_df['turn_counts'] = [[]] * len(games_df)
games_df['device_id'] = [None] * len(games_df)
games_df['format'] = args['format']
games_df['created_on'] = datetime.now().isoformat()
games_df['finished_on'] = [None] * len(games_df)

print(games_df.head())

# Prepare a CSV buffer from the DataFrame
csv_buffer = io.StringIO()
games_df.to_csv(csv_buffer, index=False, header=False, sep='\t', na_rep='\\N')
csv_buffer.seek(0)

cur.copy_from(csv_buffer, 'games', sep='\t')
conn.commit()
print("Successfully uploaded games to database")

cur.close()
conn.close()
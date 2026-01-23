#!/usr/bin/env python3
"""Get betting odds and calculate payout for Mavericks @ Knicks game."""
import psycopg2
import os

conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST", "postgres"),
    port=5432,
    database=os.getenv("POSTGRES_DB", "nba_analytics"),
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD", "postgres"),
)
cur = conn.cursor()

# Check columns in betting_odds table
cur.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'raw_dev'
    AND table_name = 'betting_odds'
    ORDER BY ordinal_position
""")
cols = cur.fetchall()
print("Columns in raw_dev.betting_odds:")
for col in cols:
    print(f"  {col[0]} ({col[1]})")

print("\n" + "=" * 80)

# Get odds for the Mavericks @ Knicks game
game_id = '0022500016'  # Mavericks @ Knicks on 1/19

cur.execute("""
    SELECT *
    FROM raw_dev.betting_odds
    WHERE game_id = %s
    ORDER BY captured_at DESC
    LIMIT 5
""", [game_id])

odds_rows = cur.fetchall()
if odds_rows:
    print(f"\nFound {len(odds_rows)} betting odds records for game {game_id}")
    print("\nSample record structure:")
    if cols:
        for i, col in enumerate(cols):
            if i < len(odds_rows[0]):
                print(f"  {col[0]}: {odds_rows[0][i]}")
    
    # Try to find moneyline odds for Mavericks (away team)
    print("\n" + "=" * 80)
    print("MONEYLINE ODDS FOR MAVERICKS (Away Team):")
    print("-" * 80)
    
    # Check what market_type values exist
    cur.execute("""
        SELECT DISTINCT market_type, book_name
        FROM raw_dev.betting_odds
        WHERE game_id = %s
    """, [game_id])
    markets = cur.fetchall()
    print(f"\nAvailable market types: {[m[0] for m in markets]}")
    print(f"Available books: {[m[1] for m in markets]}")
    
    # Try to get moneyline odds (2way market type is moneyline)
    cur.execute("""
        SELECT 
            book_name,
            market_type,
            home_odds,
            away_odds,
            home_line,
            away_line,
            captured_at
        FROM raw_dev.betting_odds
        WHERE game_id = %s
        AND market_type = '2way'
        AND away_odds IS NOT NULL
        ORDER BY captured_at DESC
        LIMIT 1
    """, [game_id])
    
    ml_odds = cur.fetchone()
    if ml_odds:
        book_name, market_type, home_odds, away_odds, home_line, away_line, captured_at = ml_odds
        print(f"\n💰 BETTING ODDS from {book_name}:")
        print(f"  Market Type: {market_type or 'Moneyline'}")
        print(f"  Knicks (Home) Odds: {home_odds}")
        print(f"  Mavericks (Away) Odds: {away_odds}")
        print(f"  Captured At: {captured_at}")
        
        # Calculate payout for $10 bet on Mavericks
        if away_odds:
            # Check if decimal odds (European format) or American format
            if away_odds > 0 and away_odds < 10:
                # Decimal odds format: odds of 1.952 means bet $1 to get $1.952 back
                total_payout = 10 * away_odds
                profit = total_payout - 10
                odds_format = "Decimal (European)"
            elif away_odds > 0:
                # American odds format: positive = underdog (bet $100 to win $odds)
                total_payout = 10 + (10 * away_odds / 100)
                profit = 10 * away_odds / 100
                odds_format = "American (Underdog)"
            else:
                # American odds format: negative = favorite (bet $|odds| to win $100)
                total_payout = 10 + (10 * 100 / abs(away_odds))
                profit = 10 * 100 / abs(away_odds)
                odds_format = "American (Favorite)"
            
            print(f"\n💰 BETTING CALCULATION:")
            print(f"  Bet Amount: $10.00")
            print(f"  Odds: {away_odds} ({odds_format})")
            print(f"  Profit: ${profit:.2f}")
            print(f"  Total Payout: ${total_payout:.2f}")
    else:
        print("\n⚠️  No moneyline odds found. Showing all available odds:")
        cur.execute("""
            SELECT book_name, market_type, home_odds, away_odds, captured_at
            FROM raw_dev.betting_odds
            WHERE game_id = %s
            ORDER BY captured_at DESC
            LIMIT 10
        """, [game_id])
        all_odds = cur.fetchall()
        for row in all_odds:
            print(f"  {row[0]} - {row[1]}: Home={row[2]}, Away={row[3]}")
else:
    print(f"\n⚠️  No betting odds found for game {game_id}")
    print("Checking if we have any betting odds data at all...")
    cur.execute("SELECT COUNT(*), MIN(captured_at::date), MAX(captured_at::date) FROM raw_dev.betting_odds")
    stats = cur.fetchone()
    print(f"  Total odds records: {stats[0]}")
    print(f"  Date range: {stats[1]} to {stats[2]}")

conn.close()

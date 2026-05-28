"""
Helper script to make a user a legendary pirate.

Usage:
    python make_legendary_pirate.py <env_file> <user_telegram_id> <epithet> <reason>

Example:
    python make_legendary_pirate.py .env 123456789 "Red-Haired" "Exceptional gameplay and contribution"
"""

import os
import sys
import asyncio

# Save the actual script arguments
script_args = sys.argv[1:]

if len(script_args) < 3:
    print("Usage: python make_legendary_pirate.py <env_file> <user_telegram_id> <epithet> <reason>")
    print("       python make_legendary_pirate.py <user_telegram_id> <epithet> <reason>  # uses .env if present")
    print("\nExample:")
    print('  python make_legendary_pirate.py .env 123456789 "Red-Haired" "Exceptional gameplay"')
    print('  python make_legendary_pirate.py 123456789 "Red-Haired" "Exceptional gameplay"')
    sys.exit(1)

# Detect whether the first arg is an env file or a user ID.
if os.path.isfile(script_args[0]):
    env_file = script_args[0]
    user_args = script_args[1:]
elif os.path.isfile(".env"):
    env_file = ".env"
    user_args = script_args
else:
    print("Environment file not found. Provide the path to your env file as the first argument or create a .env file.")
    sys.exit(1)

sys.argv = [sys.argv[0], env_file]

# Now import models after environment is set up
from src.model.User import User
from src.model.LegendaryPirate import LegendaryPirate
from resources.Database import Database

# Save the user arguments for later
script_args = user_args


async def make_user_legendary_pirate(user_tg_id: str, epithet: str, reason: str) -> bool:
    """
    Make a user a legendary pirate.
    
    :param user_tg_id: The Telegram user ID
    :param epithet: The pirate's epithet/title
    :param reason: The reason for becoming a legendary pirate
    :return: True if successful, False otherwise
    """
    
    try:
        # Initialize database connection
        db = Database().get_db()
        
        # Get the user
        user = User.get(User.tg_user_id == user_tg_id)
        
        # Check if already a legendary pirate
        if user.is_legendary_pirate():
            print(f"❌ User {user.tg_user_id} (@{user.tg_username}) is already a Legendary Pirate")
            return False
        
        # Create the legendary pirate record
        legendary_pirate = LegendaryPirate.create(
            user=user,
            epithet=epithet,
            reason=reason
        )
        
        print(f"✅ Successfully made {user.tg_user_id} (@{user.tg_username}) a Legendary Pirate!")
        print(f"   Epithet: {epithet}")
        print(f"   Reason: {reason}")
        print(f"   Legendary Pirate ID: {legendary_pirate.id}")
        
        db.close()
        return True
        
    except User.DoesNotExist:
        print(f"❌ User with Telegram ID {user_tg_id} not found")
        return False
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


def main():
    """Main entry point for the script."""
    
    if len(script_args) < 3:
        print("Usage: python make_legendary_pirate.py <env_file> <user_telegram_id> <epithet> <reason>")
        print("       python make_legendary_pirate.py <user_telegram_id> <epithet> <reason>  # uses .env if present")
        print("\nExample:")
        print('  python make_legendary_pirate.py .env 123456789 "Red-Haired" "Exceptional gameplay"')
        print('  python make_legendary_pirate.py 123456789 "Red-Haired" "Exceptional gameplay"')
        sys.exit(1)
    
    user_tg_id = script_args[0]
    epithet = script_args[1]
    reason = " ".join(script_args[2:])
    
    # Run the async function
    success = asyncio.run(make_user_legendary_pirate(user_tg_id, epithet, reason))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

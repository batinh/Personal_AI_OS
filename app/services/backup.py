import os
import shutil
import logging
from datetime import datetime

logger = logging.getLogger("AI_COACH")

def perform_backup():
    """
    Compress the entire 'data/' directory into a ZIP file and save to 'backups/'.
    Automatically delete old backups, retaining only the 7 most recent (1-week rotation).
    """
    source_dir = "data"
    backup_dir = "backups"
    
    # Create backups directory if it doesn't exist
    os.makedirs(backup_dir, exist_ok=True)
    
    # Generate filename based on current timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"coach_data_{timestamp}"
    backup_path = os.path.join(backup_dir, backup_filename)
    
    try:
        # Compress the data/ directory
        shutil.make_archive(backup_path, 'zip', source_dir)
        logger.info(f"[BACKUP] Successfully backed up: {backup_filename}.zip")
        
        # Cleanup: Keep only the 7 most recent backups to save T440 disk space
        all_backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.zip')])
        while len(all_backups) > 7:
            oldest_file = all_backups.pop(0)
            os.remove(os.path.join(backup_dir, oldest_file))
            logger.info(f"[BACKUP] Deleted old backup: {oldest_file}")
            
    except Exception as e:
        logger.error(f"[BACKUP] Error during data backup: {e}")
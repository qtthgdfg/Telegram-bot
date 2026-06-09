# utils/state_manager.py
"""
Bot State Manager - Handles graceful shutdown and state handoff
between GitHub Actions workflow runs.
"""
import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

STATE_FILE = "bot_state.json"

class BotState:
    def __init__(self):
        self.state = self.load_state()
        self.setup_signal_handlers()
    
    def load_state(self):
        """Load state from previous run"""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                print(f"📂 Loaded previous state from: {state.get('last_run', 'unknown')}")
                return state
            except:
                pass
        
        # Fresh state
        return {
            "first_run": True,
            "last_message_id": 0,
            "last_channel_messages": {},
            "processed_signals": [],
            "total_trades": 0,
            "successful_trades": 0,
            "last_run": None,
            "runs_count": 0
        }
    
    def setup_signal_handlers(self):
        """Catch shutdown signals to save state"""
        def graceful_shutdown(signum, frame):
            print("\n⏰ Shutdown signal received! Saving state...")
            self.save_state()
            print("✅ State saved. Bot can safely restart.")
            sys.exit(0)
        
        signal.signal(signal.SIGTERM, graceful_shutdown)
        signal.signal(signal.SIGINT, graceful_shutdown)
    
    def save_state(self):
        """Save current state to file"""
        self.state["last_run"] = datetime.now().isoformat()
        self.state["runs_count"] += 1
        self.state["first_run"] = False
        
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=2, default=str)
        
        print(f"💾 State saved: {self.state['runs_count']} runs completed")
    
    def update_last_message(self, channel, message_id):
        """Track last processed message per channel"""
        self.state["last_channel_messages"][str(channel)] = message_id
        self.state["last_message_id"] = message_id
        
        # Save periodically (every 100 messages)
        if message_id % 100 == 0:
            self.save_state()
    
    def get_last_message_id(self, channel=None):
        """Get last processed message ID"""
        if channel:
            return self.state["last_channel_messages"].get(str(channel), 0)
        return self.state.get("last_message_id", 0)
    
    def add_signal(self, signal_data):
        """Track processed signals"""
        signal_id = signal_data.get("id") or hash(str(signal_data))
        if signal_id not in self.state["processed_signals"]:
            self.state["processed_signals"].append(signal_id)
            # Keep only last 1000 to avoid huge files
            if len(self.state["processed_signals"]) > 1000:
                self.state["processed_signals"] = self.state["processed_signals"][-500:]
    
    def is_signal_processed(self, signal_data):
        """Check if signal was already processed"""
        signal_id = signal_data.get("id") or hash(str(signal_data))
        return signal_id in self.state["processed_signals"]
    
    def record_trade(self, success=True):
        """Track trade statistics"""
        self.state["total_trades"] += 1
        if success:
            self.state["successful_trades"] += 1
    
    def get_summary(self):
        """Get bot run summary"""
        return f"""
📊 Bot Statistics:
   Runs: {self.state['runs_count']}
   Trades: {self.state['total_trades']}
   Success rate: {self.state['successful_trades']}/{self.state['total_trades']}
   Last run: {self.state['last_run']}
   Channels tracked: {len(self.state['last_channel_messages'])}
"""

# Global instance
bot_state = BotState()

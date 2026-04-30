class AppState:
    """
    Global Application State Management using Singleton Pattern.
    Holds temporary runtime state variables (e.g., service pause/resume).
    """

    _instance = None
    service_active = True
    # Telemetry counters
    chunked_send_count = 0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AppState, cls).__new__(cls)
        return cls._instance

    def increment_chunked_send_count(self, amount: int = 1):
        try:
            self.chunked_send_count += amount
        except Exception:
            # Defensive: ensure counter exists
            self.chunked_send_count = getattr(self, "chunked_send_count", 0) + amount


# Singleton instance exported for global use
state = AppState()

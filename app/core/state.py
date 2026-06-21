class AppState:
    """
    Global Application State Management using Singleton Pattern.
    Holds temporary runtime state variables (e.g., service pause/resume).
    """

    _instance = None
    service_active = True

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AppState, cls).__new__(cls)
        return cls._instance


# Singleton instance exported for global use
state = AppState()

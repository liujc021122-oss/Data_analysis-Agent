class RecordingExecutor:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir
        self.calls = []

    def execute_code(self, code):
        self.calls.append(code)
        if "raise ValueError" in code:
            return {
                "success": False,
                "output": "",
                "error": "planned executor failure",
                "variables": {},
            }
        return {
            "success": True,
            "output": "5",
            "error": "",
            "variables": {},
        }

    def set_variable(self, name, value):
        return None

    def set_sensitive_columns(self, names):
        return None

    def get_environment_info(self):
        return "offline executor"

    def reset_environment(self):
        self.calls.clear()

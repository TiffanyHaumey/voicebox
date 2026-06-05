import queue

class Params:
    def __init__(self):
        self._preset_q = queue.SimpleQueue()
        self.monitoring = False       # plain bool, GIL-atomic read/write
        self.master_gain_db = 0.0     # plain float
    def request_preset(self, name: str):
        self._preset_q.put(name)
    def poll_preset(self):
        """Audio thread calls this once per block. Returns most recent request or None."""
        name = None
        try:
            while True:
                name = self._preset_q.get_nowait()
        except queue.Empty:
            pass
        return name

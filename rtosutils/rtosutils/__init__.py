import logging

from rich import inspect
from rich.table import Table
from rich.pretty import Pretty
from rich import box

from protorpc.util import CallsetBase

from rtosutils.lib.rtosutils import ThreadInfo

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


STATES_TABLE = {
    1: 'dummy',
    2: 'pending',
    4: 'prestart',
    8: 'dead',
    16: 'suspended',
    32: 'aborting',
    64: 'suspending',
    128: 'queued'
}


def state_str(state_mask):
    for k in range(8):
        bit = 0x1 << k
        if state_mask & bit:
            return STATES_TABLE[bit]
    return 'unknown'


class RtosUtilsException(Exception):
    pass


class RtosUtils(CallsetBase):
    """Class which provides access to the RtosUtilsRpc callset.
    """
    name = "rtosutils"

    def __init__(self, api):
        super().__init__(api)

    def get_system_threads_table(
        self,
        thread_info,
        total_cycles
    ) -> Table:

        table = Table(title='RTOS Threads', box=box.MINIMAL_DOUBLE_HEAD)

        table.add_column('#', style='magenta')
        table.add_column('Name', style='yellow')
        table.add_column('TID', style='cyan')
        table.add_column('Prio')
        table.add_column('State', style='magenta')
        table.add_column('Peak Cycles')
        table.add_column('Avg Cycles')
        table.add_column('Peak/Avg')
        table.add_column('Load %')
        table.add_column('Stack Size')
        table.add_column('Stack Avail')
        table.add_column('Stack Used %')

        for k, entry in enumerate(thread_info):
            row = []
            row.append(Pretty(k))
            row.append(entry.name)
            row.append(f"0x{entry.tid:08x}")
            row.append(Pretty(entry.prio))
            row.append(state_str(entry.state))
            row.append(f"{entry.peak_cycles:.1e}")
            row.append(f"{entry.avg_cycles:.1e}")
            peak_to_avg = entry.peak_cycles / entry.avg_cycles
            row.append(f"{peak_to_avg:.1f}")
            load_pct = (entry.total_cycles / total_cycles)*100
            row.append(f"{load_pct:.1f}")
            row.append(Pretty(entry.stack_size))
            row.append(Pretty(entry.unused_stack))
            stack_used_pct = ((entry.stack_size - entry.unused_stack)/entry.stack_size)*100
            row.append(f"{stack_used_pct:.1f}")

            table.add_row(*row)

        return table

    def collect_thread_info(self):
        """Collects all thread info from target.
        """
        thread_info = []
        start_idx = 0
        result = self.get_system_threads(start_idx)
        num_threads = result.num_threads
        thread_info += result.thread_info
        num_threads_collected = len(result.thread_info)

        while num_threads_collected < num_threads:
            start_idx += len(result.thread_info)
            result = self.get_system_threads(start_idx)
            thread_info += result.thread_info
            num_threads_collected += len(result.thread_info)

        return thread_info, result.total_cycles

    def get_system_threads(self, idx_start):
        reply = self.api.get_system_threads(idx_start=idx_start)
        self.check_reply(reply)
        #inspect(reply.result)
        return reply.result

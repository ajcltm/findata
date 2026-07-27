import asyncio
import queue
import threading
import logging


class KisQueueBridge:

    def __init__(self):
        self.logger = logging.getLogger("kis")
        self.thread_q = queue.Queue()

    async def bridge_async_to_thread(self, async_q):
        while True:
            item = await async_q.get()      # async에서 꺼내고
            self.thread_q.put(item)              # thread-safe 큐로 전달
            async_q.task_done()

    def worker_thread(self):
        while True:
            item = self.thread_q.get()           # 스레드에서 blocking get
            self.logger.info("asynce q data into thread q")
            print("thread got:", item)
            self.thread_q.task_done()

    async def run(self, async_q):
        # threading.Thread(target=self.worker_thread, daemon=True).start()
        print("----bridge thrediing start-----")
        queue_task = asyncio.create_task(self.bridge_async_to_thread(async_q))
        await queue_task
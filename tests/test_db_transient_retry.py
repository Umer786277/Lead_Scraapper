import unittest

from psycopg import errors as pg_errors

import db


class TransientRetryTests(unittest.TestCase):
    def test_retries_deadlock_then_succeeds(self):
        calls = []

        def operation():
            calls.append(1)
            if len(calls) == 1:
                raise pg_errors.DeadlockDetected("deadlock detected")
            return "ok"

        result = db._retry_transient(operation, attempts=3, base_delay=0)

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 2)

    def test_reraises_after_last_attempt(self):
        calls = []

        def operation():
            calls.append(1)
            raise pg_errors.DeadlockDetected("deadlock detected")

        with self.assertRaises(pg_errors.DeadlockDetected):
            db._retry_transient(operation, attempts=2, base_delay=0)

        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

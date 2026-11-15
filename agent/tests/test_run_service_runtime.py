"""Run ownership, replay, hard budgets and bounded process resources."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from time import monotonic
import unittest

from agent.runtime.budgets import InferenceGate, RunBudget
from agent.runtime.errors import WorkflowError
from agent.testing.service import ControlledExecutor, controlled_registry, service_run


def deadline(run, seconds):
    run["execution"]["deadline_at"] = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


class RunTests(unittest.IsolatedAsyncioTestCase):
    def registry(self, executor=None, **options):
        registry = controlled_registry(executor, **options)
        self.addAsyncCleanup(registry.close, 0.1)
        return registry

    async def accepted(self, registry, run, executor):
        task = asyncio.create_task(registry.submit(run, request_id="test-first"))
        await asyncio.wait_for(executor.started.wait(), 2)
        return task

    def assert_code(self, caught, code):
        self.assertEqual(caught.exception.code, code)

    async def test_completed_replay_uses_body_value_and_detached_snapshots(self):
        executor = ControlledExecutor()
        registry, run = self.registry(executor), service_run()
        first = await registry.submit(run, request_id="trace-one")
        reordered = {key: run[key] for key in reversed(run)}
        self.assertEqual(await registry.submit(reordered, request_id="trace-two"), first)
        first["result"]["answer"]["text"] = "Caller mutation"
        stored = await registry.get(run["run_id"])
        self.assertNotEqual(stored, first)
        stored["result"]["catalog_snapshot"].clear()
        self.assertEqual(len((await registry.get(run["run_id"]))["result"]["catalog_snapshot"]), 3)
        self.assertEqual(len(executor.calls), 1)

    async def test_processing_and_changed_body_never_start_another_execution(self):
        executor = ControlledExecutor(blocked=True)
        registry, run = self.registry(executor), service_run()
        original = deepcopy(run)
        task = await self.accepted(registry, run, executor)
        self.assertEqual((await registry.get(run["run_id"]))["state"], "processing")
        with self.assertRaises(WorkflowError) as caught:
            await registry.submit(run, request_id="duplicate")
        self.assert_code(caught, "agent_run_in_progress")
        for field in ("question", "deadline", "profile", "language", "revision"):
            changed = deepcopy(original)
            if field == "deadline":
                deadline(changed, 40)
            elif field == "profile":
                changed["execution"]["profile_id"] = "unsupported-profile"
            elif field == "language":
                changed["language_override"] = "es"
            elif field == "revision":
                changed["conversation_state"]["revision"] = 1
                changed["conversation_state"]["last_answer_language"] = "en"
            else:
                changed["question"] = "A different question"
            with self.subTest(field=field), self.assertRaises(WorkflowError) as caught:
                await registry.submit(changed, request_id="changed")
            self.assert_code(caught, "agent_run_conflict")
        run["context"]["artist"] = "Mutated outside"
        executor.release.set()
        self.assertEqual((await task)["state"], "completed")
        self.assertEqual(executor.calls[0][0], original)
        self.assertEqual(len(executor.calls), 1)

    async def test_completed_replay_precedes_expired_deadline_and_readiness(self):
        now = [100.0]
        wall = datetime.now(timezone.utc)
        executor = ControlledExecutor()
        registry = self.registry(executor, clock=lambda: now[0], wall_clock=lambda: wall)
        run = service_run()
        first = await registry.submit(run, request_id="first")
        now[0] += 56
        registry.ready = lambda: False
        self.assertEqual(await registry.submit(run, request_id="replay"), first)
        self.assertEqual(len(executor.calls), 1)

    async def test_failed_replay_retains_typed_error_and_hides_exception_text(self):
        executor = ControlledExecutor(error=WorkflowError("retrieval_unavailable", "PRIVATE SECRET", retryable=False, phase="retrieval"))
        registry, run = self.registry(executor), service_run()
        for trace in ("first", "replay"):
            with self.assertRaises(WorkflowError) as caught:
                await registry.submit(run, request_id=trace)
            self.assert_code(caught, "retrieval_unavailable")
            self.assertTrue(caught.exception.retryable)
            self.assertNotIn("SECRET", str(caught.exception))
        snapshot = await registry.get(run["run_id"])
        self.assertEqual(snapshot["state"], "failed")
        self.assertIsNone(snapshot["result"])
        self.assertEqual(snapshot["error"]["phase"], "retrieval")
        self.assertEqual(len(executor.calls), 1)

    async def test_cancelled_post_waiter_does_not_cancel_accepted_run(self):
        executor = ControlledExecutor(blocked=True)
        registry, run = self.registry(executor), service_run()
        task = await self.accepted(registry, run, executor)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(executor.cancelled.is_set())
        self.assertEqual((await registry.get(run["run_id"]))["state"], "processing")
        executor.release.set()
        await asyncio.wait_for(registry._runs[run["run_id"]].task, 2)
        self.assertEqual((await registry.get(run["run_id"]))["state"], "completed")
        self.assertEqual(len(executor.calls), 1)

    async def test_parent_deadline_cancels_work_and_records_failed_run(self):
        executor = ControlledExecutor(blocked=True)
        registry, run = self.registry(executor), service_run()
        deadline(run, 1)
        with self.assertRaises(WorkflowError) as caught:
            await registry.submit(run, request_id="timeout")
        self.assert_code(caught, "request_timeout")
        self.assertTrue(executor.cancelled.is_set())
        self.assertEqual((await registry.get(run["run_id"]))["error"]["code"], "request_timeout")

    async def test_late_cancellation_suppressing_result_cannot_become_success(self):
        executor = ControlledExecutor(blocked=True, ignore_cancel=True)
        registry, run = self.registry(executor), service_run()
        deadline(run, 1)
        task = await self.accepted(registry, run, executor)
        await asyncio.wait_for(executor.cancelled.wait(), 2)
        with self.assertRaises(WorkflowError) as caught:
            await asyncio.wait_for(asyncio.shield(task), 0.2)
        self.assert_code(caught, "request_timeout")
        snapshot = await registry.get(run["run_id"])
        self.assertEqual(snapshot["error"]["code"], "request_timeout")
        executor.release.set()
        with self.assertRaises(WorkflowError) as caught:
            await task
        self.assert_code(caught, "request_timeout")
        self.assertEqual((await registry.get(run["run_id"]))["state"], "failed")

    async def test_capacity_rejection_does_not_reserve_run_id(self):
        executor = ControlledExecutor(blocked=True)
        registry = self.registry(executor)
        runs = [service_run() for _ in range(9)]
        tasks = [asyncio.create_task(registry.submit(run, request_id="capacity")) for run in runs[:8]]
        await asyncio.wait_for(executor.started.wait(), 2)
        with self.assertRaises(WorkflowError) as caught:
            await registry.submit(runs[8], request_id="ninth")
        self.assert_code(caught, "agent_busy")
        with self.assertRaises(WorkflowError) as caught:
            await registry.get(runs[8]["run_id"])
        self.assert_code(caught, "agent_run_not_found")
        executor.release.set()
        self.assertTrue(all(x["state"] == "completed" for x in await asyncio.gather(*tasks)))
        self.assertEqual((await registry.submit(runs[8], request_id="retry"))["state"], "completed")
        self.assertEqual(len(executor.calls), 9)

    async def test_timed_out_worker_keeps_capacity_until_it_exits(self):
        executor = ControlledExecutor(blocked=True, ignore_cancel=True)
        registry, run = self.registry(executor, active_limit=1), service_run()
        deadline(run, 1)
        with self.assertRaises(WorkflowError) as caught:
            await registry.submit(run, request_id="timed-out-worker")
        self.assert_code(caught, "request_timeout")
        await asyncio.wait_for(executor.cancelled.wait(), 1)
        with self.assertRaises(WorkflowError) as caught:
            await registry.submit(service_run(), request_id="still-full")
        self.assert_code(caught, "agent_busy")
        executor.release.set()
        await asyncio.wait_for(registry._runs[run["run_id"]].worker, 1)
        self.assertEqual((await registry.submit(service_run(), request_id="available"))["state"], "completed")

    async def test_oldest_terminal_eviction_and_ttl_preserve_active_run(self):
        now = [100.0]
        executor = ControlledExecutor()
        registry = self.registry(executor, terminal_limit=2, retention_seconds=10, clock=lambda: now[0])
        runs = [service_run() for _ in range(4)]
        for run in runs[:3]:
            await registry.submit(run, request_id="terminal")
            now[0] += 1
        with self.assertRaises(WorkflowError) as caught:
            await registry.get(runs[0]["run_id"])
        self.assert_code(caught, "agent_run_not_found")
        executor.release.clear()
        executor.started.clear()
        active = await self.accepted(registry, runs[3], executor)
        now[0] += 10
        for run in runs[1:3]:
            with self.assertRaises(WorkflowError) as caught:
                await registry.get(run["run_id"])
            self.assert_code(caught, "agent_run_not_found")
        self.assertEqual((await registry.get(runs[3]["run_id"]))["state"], "processing")
        executor.release.set()
        await active

    async def test_shutdown_stops_admission_and_drains_successful_work(self):
        executor = ControlledExecutor(blocked=True)
        registry, run = self.registry(executor), service_run()
        task = await self.accepted(registry, run, executor)
        closing = asyncio.create_task(registry.close(1))
        await asyncio.sleep(0)
        self.assertFalse(registry.is_ready())
        with self.assertRaises(WorkflowError) as caught:
            await registry.submit(service_run(), request_id="closing")
        self.assert_code(caught, "service_not_ready")
        executor.release.set()
        await closing
        self.assertEqual((await task)["state"], "completed")

    async def test_forced_shutdown_cancels_and_keeps_failure(self):
        executor = ControlledExecutor(blocked=True, ignore_cancel=True)
        registry, run = self.registry(executor), service_run()
        task = await self.accepted(registry, run, executor)
        await registry.close(0.05)
        self.assertEqual((await registry.get(run["run_id"]))["error"]["code"], "service_not_ready")
        executor.release.set()
        with self.assertRaises(WorkflowError) as caught:
            await task
        self.assert_code(caught, "service_not_ready")
        self.assertEqual((await registry.get(run["run_id"]))["state"], "failed")

    async def test_expired_new_run_and_unsupported_contract_are_not_reserved(self):
        registry = self.registry()
        for code in ("request_timeout", "agent_contract_mismatch"):
            run = service_run()
            if code == "request_timeout":
                deadline(run, -1)
            else:
                run["execution"]["profile_id"] = "not-supported"
            with self.subTest(code=code), self.assertRaises(WorkflowError) as caught:
                await registry.submit(run, request_id="reject")
            self.assert_code(caught, code)
            with self.assertRaises(WorkflowError):
                await registry.get(run["run_id"])

    async def test_semantic_scope_and_cache_checks_run_before_execution(self):
        executor = ControlledExecutor()
        registry = self.registry(executor)
        run = service_run()
        run["context"]["artist"] = "Wrong artist"
        with self.assertRaises(WorkflowError) as caught:
            await registry.submit(run, request_id="scope")
        self.assert_code(caught, "agent_scope_mismatch")
        self.assertEqual(executor.calls, [])

    async def test_invalid_or_oversized_executor_output_is_safe_terminal_failure(self):
        mutations = [lambda value: value["scope"].update(artwork_id="wrong-work"),
                     lambda value: value["execution"].update(model_name="x" * 200000),
                     lambda value: value.update(extra="x" * (256 * 1024))]
        for mutate in mutations:
            registry, run = self.registry(ControlledExecutor(mutate=mutate)), service_run()
            with self.subTest(mutate=mutate), self.assertRaises(WorkflowError) as caught:
                await registry.submit(run, request_id="bad-output")
            self.assert_code(caught, "agent_internal_error")
            self.assertEqual((await registry.get(run["run_id"]))["state"], "failed")

    async def test_unknown_executor_exception_is_sanitized(self):
        registry, run = self.registry(ControlledExecutor(error=RuntimeError("PRIVATE PROMPT"))), service_run()
        with self.assertRaises(WorkflowError) as caught:
            await registry.submit(run, request_id="error")
        self.assert_code(caught, "agent_internal_error")
        self.assertNotIn("PRIVATE", (await registry.get(run["run_id"]))["error"]["message"])


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_utc_deadline_is_capped_and_parent_expiration_wins_cancellation(self):
        wall = datetime(2026, 11, 14, tzinfo=timezone.utc)
        now = [100.0]
        budget = RunBudget.from_utc("2026-11-14T00:10:00Z", clock=lambda: now[0], wall_clock=lambda: wall)
        self.assertEqual(budget.deadline, 155)
        budget.cancel("service_not_ready")
        now[0] = 155
        with self.assertRaises(WorkflowError) as caught:
            budget.check_active()
        self.assertEqual(caught.exception.code, "request_timeout")

    async def test_shared_inference_gate_covers_slot_wait_and_releases_on_cancellation(self):
        started, release, entered = asyncio.Event(), asyncio.Event(), []

        class Model:
            async def complete_structured(self, **arguments):
                entered.append(arguments["operation"])
                started.set()
                await release.wait()
                return "synthetic"

        gate = InferenceGate()
        first, second = gate.wrap(Model()), gate.wrap(Model())

        def call(model, operation, seconds=2):
            return model.complete_structured(operation=operation, messages=[], output_schema={},
                                             profile=None, deadline=monotonic() + seconds, trace_context={})

        pending = asyncio.create_task(call(first, "plan"))
        await started.wait()
        with self.assertRaises(WorkflowError) as caught:
            await call(second, "assess", 0.02)
        self.assertEqual((caught.exception.code, caught.exception.phase), ("generation_timeout", "assessment"))
        self.assertEqual(entered, ["plan"])
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        release.set()
        self.assertEqual(await call(second, "final"), "synthetic")
        self.assertEqual(entered, ["plan", "final"])

    async def test_inference_gate_rejects_late_provider_completion_without_retry(self):
        calls = []

        class Model:
            async def complete_structured(self, **arguments):
                calls.append(arguments)
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    return "late"

        model = InferenceGate().wrap(Model())
        with self.assertRaises(WorkflowError) as caught:
            await model.complete_structured(operation="plan", messages=[], output_schema={},
                                             profile=None, deadline=monotonic() + 0.02, trace_context={})
        self.assertEqual(caught.exception.code, "generation_timeout")
        self.assertEqual(len(calls), 1)

    async def test_noncooperative_provider_keeps_shared_slot_after_stage_timeout(self):
        release, cancelled = asyncio.Event(), asyncio.Event()
        calls = []

        class Model:
            async def complete_structured(self, **arguments):
                calls.append(arguments)
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                return "late synthetic response"

        gate = InferenceGate()
        model = gate.wrap(Model())

        async def call():
            return await model.complete_structured(operation="plan", messages=[], output_schema={},
                profile=None, deadline=monotonic() + 0.05, trace_context={})

        with self.assertRaises(WorkflowError):
            await asyncio.wait_for(call(), 1)
        await cancelled.wait()
        with self.assertRaises(WorkflowError):
            await call()
        self.assertEqual(len(calls), 1)
        release.set()
        await asyncio.gather(*gate._inflight)
        await asyncio.sleep(0)
        self.assertEqual(await call(), "late synthetic response")


if __name__ == "__main__":
    unittest.main()

import logging

from teuthology.task.tests import log_test_results
from teuthology.exceptions import CommandFailedError

log = logging.getLogger(__name__)


class TestExceptions(object):

    @log_test_results
    def test_command_failed_label(self, ctx, config):
        result = ""
        try:
            self.force_command_failure(ctx, config)
        except CommandFailedError as e:
            result = str(e)

        assert "working as expected" in result

    def force_command_failure(self, ctx, config):
        log.info("forcing a command failure...")
        ctx.cluster.run(
            args=["python", "-c", "assert False"],
            label="working as expected, nothing to see here"
        )

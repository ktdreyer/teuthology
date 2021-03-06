import os
import sys
import time
import yaml
import logging
import subprocess
from textwrap import dedent
from textwrap import fill

import teuthology
from teuthology.config import config
from teuthology import misc
from teuthology import ls
from .job_status import get_status
from .report import ResultsSerializer

log = logging.getLogger(__name__)


def main(args):

    log = logging.getLogger(__name__)
    if args['--verbose']:
        teuthology.log.setLevel(logging.DEBUG)

    if not args['--dry-run']:
        log_path = os.path.join(args['--archive-dir'], 'results.log')
        teuthology.setup_log_file(log_path)

    try:
        results(args['--archive-dir'], args['--name'], args['--email'],
                int(args['--timeout']), args['--dry-run'])
    except Exception:
        log.exception('error generating results')
        raise


def results(archive_dir, name, email, timeout, dry_run):
    archive_base = os.path.split(archive_dir)[0]
    serializer = ResultsSerializer(archive_base)
    starttime = time.time()

    if timeout:
        log.info('Waiting up to %d seconds for tests to finish...', timeout)
    while serializer.running_jobs_for_run(name) and timeout > 0:
        if time.time() - starttime > timeout:
            log.warn('test(s) did not finish before timeout of %d seconds',
                     timeout)
            break
        time.sleep(10)
    log.info('Tests finished! gathering results...')

    (subject, body) = build_email_body(name, archive_dir)

    try:
        if email and dry_run:
            print "From: %s" % (config.results_sending_email or 'teuthology')
            print "To: %s" % email
            print "Subject: %s" % subject
            print body
        elif email:
            email_results(
                subject=subject,
                from_=(config.results_sending_email or 'teuthology'),
                to=email,
                body=body,
            )
    finally:
        generate_coverage(archive_dir, name)


def generate_coverage(archive_dir, name):
    coverage_config_keys = ('coverage_output_dir', 'coverage_html_dir',
                            'coverage_tools_dir')
    for key in coverage_config_keys:
        if key not in config.to_dict():
            log.warn(
                "'%s' not in teuthology config; skipping coverage report",
                key)
            return
    log.info('starting coverage generation')
    subprocess.Popen(
        args=[
            os.path.join(os.path.dirname(sys.argv[0]), 'teuthology-coverage'),
            '-v',
            '-o',
            os.path.join(config.coverage_output_dir, name),
            '--html-output',
            os.path.join(config.coverage_html_dir, name),
            '--cov-tools-dir',
            config.coverage_tools_dir,
            archive_dir,
        ],
    )


def email_results(subject, from_, to, body):
    log.info('Sending results to {to}: {body}'.format(to=to, body=body))
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = from_
    msg['To'] = to
    log.debug('sending email %s', msg.as_string())
    smtp = smtplib.SMTP('localhost')
    smtp.sendmail(msg['From'], [msg['To']], msg.as_string())
    smtp.quit()


def build_email_body(name, archive_dir):
    failed = {}
    hung = {}
    passed = {}

    for job in ls.get_jobs(archive_dir):
        job_dir = os.path.join(archive_dir, job)
        summary_file = os.path.join(job_dir, 'summary.yaml')

        # Every job gets a link to e.g. pulpito's pages
        info_url = misc.get_results_url(name, job)
        if info_url:
            info_line = email_templates['info_url_templ'].format(info=info_url)
        else:
            info_line = ''

        # Unfinished jobs will have no summary.yaml
        if not os.path.exists(summary_file):
            info_file = os.path.join(job_dir, 'info.yaml')

            desc = ''
            if os.path.exists(info_file):
                with file(info_file) as f:
                    info = yaml.safe_load(f)
                    desc = info['description']

            hung[job] = email_templates['hung_templ'].format(
                job_id=job,
                desc=desc,
                info_line=info_line,
            )
            continue

        with file(summary_file) as f:
            summary = yaml.safe_load(f)

        if get_status(summary) == 'pass':
            passed[job] = email_templates['pass_templ'].format(
                job_id=job,
                desc=summary.get('description'),
                time=int(summary.get('duration', 0)),
                info_line=info_line,
            )
        else:
            log = misc.get_http_log_path(archive_dir, job)
            if log:
                log_line = email_templates['fail_log_templ'].format(log=log)
            else:
                log_line = ''
            # Transitioning from sentry_events -> sentry_event
            sentry_events = summary.get('sentry_events')
            if sentry_events:
                sentry_event = sentry_events[0]
            else:
                sentry_event = summary.get('sentry_event', '')
            if sentry_event:
                sentry_line = email_templates['fail_sentry_templ'].format(
                    sentry_event=sentry_event)
            else:
                sentry_line = ''

            # 'fill' is from the textwrap module and it collapses a given
            # string into multiple lines of a maximum width as specified. We
            # want 75 characters here so that when we indent by 4 on the next
            # line, we have 79-character exception paragraphs.
            reason = fill(summary.get('failure_reason'), 75)
            reason = '\n'.join(('    ') + line for line in reason.splitlines())

            failed[job] = email_templates['fail_templ'].format(
                job_id=job,
                desc=summary.get('description'),
                time=int(summary.get('duration', 0)),
                reason=reason,
                info_line=info_line,
                log_line=log_line,
                sentry_line=sentry_line,
            )

    maybe_comma = lambda s: ', ' if s else ' '

    subject = ''
    fail_sect = ''
    hung_sect = ''
    pass_sect = ''
    if failed:
        subject += '{num_failed} failed{sep}'.format(
            num_failed=len(failed),
            sep=maybe_comma(hung or passed)
        )
        fail_sect = email_templates['sect_templ'].format(
            title='Failed',
            jobs=''.join(failed.values())
        )
    if hung:
        subject += '{num_hung} hung{sep}'.format(
            num_hung=len(hung),
            sep=maybe_comma(passed),
        )
        hung_sect = email_templates['sect_templ'].format(
            title='Hung',
            jobs=''.join(hung.values()),
        )
    if passed:
        subject += '%s passed ' % len(passed)
        pass_sect = email_templates['sect_templ'].format(
            title='Passed',
            jobs=''.join(passed.values()),
        )

    body = email_templates['body_templ'].format(
        name=name,
        info_root=misc.get_results_url(name),
        log_root=misc.get_http_log_path(archive_dir),
        fail_count=len(failed),
        hung_count=len(hung),
        pass_count=len(passed),
        fail_sect=fail_sect,
        hung_sect=hung_sect,
        pass_sect=pass_sect,
    )

    subject += 'in {suite}'.format(suite=name)
    return (subject.strip(), body.strip())

email_templates = {
    'body_templ': dedent("""\
        Test Run: {name}
        =================================================================
        info:   {info_root}
        logs:   {log_root}
        failed: {fail_count}
        hung:   {hung_count}
        passed: {pass_count}

        {fail_sect}{hung_sect}{pass_sect}
        """),
    'sect_templ': dedent("""\
        {title}
        =================================================================
        {jobs}
        """),
    'fail_templ': dedent("""\
        [{job_id}]  {desc}
        -----------------------------------------------------------------
        time:   {time}s{info_line}{log_line}{sentry_line}

        {reason}

        """),
    'info_url_templ': "\ninfo:   {info}",
    'fail_log_templ': "\nlog:    {log}",
    'fail_sentry_templ': "\nsentry: {sentry_event}",
    'hung_templ': dedent("""\
        [{job_id}] {desc}{info_line}
        """),
    'pass_templ': dedent("""\
        [{job_id}] {desc}
        time:   {time}s{info_line}

        """),
}

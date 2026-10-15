# Nightly off-site backups (see scripts/backup/sdai-backup.sh).
# PostgreSQL 15 client tools match the db service, restic encrypts and
# deduplicates, openssh provides the SFTP transport.
FROM postgres:15-alpine

RUN apk add --no-cache restic openssh-client bash coreutils findutils

COPY sdai-backup.sh restore-test.sh backup-loop.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/sdai-backup.sh /usr/local/bin/restore-test.sh /usr/local/bin/backup-loop.sh

ENTRYPOINT ["/usr/local/bin/backup-loop.sh"]

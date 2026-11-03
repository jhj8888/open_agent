ALTER TABLE `workflow_artifact`
    ADD COLUMN `bucket_name` VARCHAR(255) DEFAULT NULL COMMENT 'Dedicated private OSS bucket; null means legacy console bucket'
    AFTER `object_key`,
    MODIFY COLUMN `object_key` VARCHAR(512) NULL COMMENT 'Object key; null means the deleted object was physically purged';

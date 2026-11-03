-- Remove the tenant credentials that were published in earlier example data.
-- The replacement is intentionally limited to the exact disclosed values so
-- user-provided credentials are never modified.
SET @legacy_tenant_key = '7b709739e8da44536127a333c7603a83';
SET @legacy_tenant_secret = 'NjhmY2NmM2NkZDE4MDFlNmM5ZjcyZjMy';

UPDATE workflow
SET published_data = REPLACE(
        REPLACE(published_data, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE published_data LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR published_data LIKE CONCAT('%', @legacy_tenant_secret, '%');

UPDATE workflow
SET `data` = REPLACE(
        REPLACE(`data`, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE `data` LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR `data` LIKE CONCAT('%', @legacy_tenant_secret, '%');

UPDATE workflow
SET bak = REPLACE(
        REPLACE(bak, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE bak LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR bak LIKE CONCAT('%', @legacy_tenant_secret, '%');

UPDATE workflow_version
SET `data` = REPLACE(
        REPLACE(`data`, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE `data` LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR `data` LIKE CONCAT('%', @legacy_tenant_secret, '%');

UPDATE workflow_version
SET sys_data = REPLACE(
        REPLACE(sys_data, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE sys_data LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR sys_data LIKE CONCAT('%', @legacy_tenant_secret, '%');

UPDATE workflow_comparison
SET `data` = REPLACE(
        REPLACE(`data`, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE `data` LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR `data` LIKE CONCAT('%', @legacy_tenant_secret, '%');

UPDATE flow_protocol_temp
SET biz_protocol = REPLACE(
        REPLACE(biz_protocol, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE biz_protocol LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR biz_protocol LIKE CONCAT('%', @legacy_tenant_secret, '%');

UPDATE flow_protocol_temp
SET sys_protocol = REPLACE(
        REPLACE(sys_protocol, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE sys_protocol LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR sys_protocol LIKE CONCAT('%', @legacy_tenant_secret, '%');

UPDATE exported_workflow_template
SET snapshot_yaml = REPLACE(
        REPLACE(snapshot_yaml, @legacy_tenant_key, ''),
        @legacy_tenant_secret,
        ''
    )
WHERE snapshot_yaml LIKE CONCAT('%', @legacy_tenant_key, '%')
   OR snapshot_yaml LIKE CONCAT('%', @legacy_tenant_secret, '%');

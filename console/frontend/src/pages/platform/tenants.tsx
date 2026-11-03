import { useEffect, useState } from 'react';
import ModelConnections from './models';
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  AuditEntry,
  Environment,
  Member,
  Membership,
  Quotas,
  Tenant,
  TenantRole,
  createTenant,
  getQuotas,
  listAudit,
  listMembers,
  listMemberships,
  listTenants,
  removeMember,
  saveQuotas,
  setMember,
  setTenantStatus,
} from '@/services/platform-tenants';

const roleNames: Record<TenantRole, string> = {
  tenant_admin: '租户管理员',
  developer: '开发者',
  operator: '运维',
  viewer: '只读',
};
const statusNames: Record<Tenant['status'], string> = {
  active: '启用',
  suspended: '已停用',
  archived: '已归档',
};
const roleOptions = Object.entries(roleNames).map(([value, label]) => ({
  value,
  label,
}));

function failure(): void {
  message.error('操作未完成，请检查权限或刷新后重试。');
}

function EnvironmentPanel({
  tenant,
  environment,
  role,
}: {
  tenant: Tenant;
  environment: Environment;
  role: TenantRole;
}): JSX.Element {
  const [form] = Form.useForm<Quotas>();
  const [quotas, setQuotas] = useState<Quotas>();
  const [members, setMembers] = useState<Member[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [memberOpen, setMemberOpen] = useState(false);
  const [memberForm] = Form.useForm<{ uid: string; role: TenantRole }>();
  const [editing, setEditing] = useState<Member>();
  const administrator = role === 'tenant_admin';
  const editable = administrator && tenant.status === 'active';

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError(false);
    const load = async (): Promise<void> => {
      try {
        const quota = await getQuotas(tenant.tenantId, environment);
        const [memberRows, auditRows] = administrator
          ? await Promise.all([
              tenant.status === 'active'
                ? listMembers(tenant.tenantId, environment)
                : Promise.resolve([]),
              listAudit(tenant.tenantId, environment),
            ])
          : [[], []];
        if (!ignore) {
          setQuotas(quota);
          form.setFieldsValue(quota);
          setMembers(memberRows);
          setAudit(auditRows);
        }
      } catch {
        if (!ignore) setError(true);
      } finally {
        if (!ignore) setLoading(false);
      }
    };
    void load();
    return () => {
      ignore = true;
    };
  }, [
    tenant.tenantId,
    tenant.status,
    environment,
    administrator,
    generation,
    form,
  ]);

  const change = async (operation: () => Promise<void>): Promise<void> => {
    setSaving(true);
    try {
      await operation();
      message.success('已保存');
      setGeneration(value => value + 1);
    } catch {
      failure();
    } finally {
      setSaving(false);
    }
  };

  if (loading)
    return (
      <Spin tip="正在加载环境配置…">
        <div style={{ height: 180 }} />
      </Spin>
    );
  if (error || !quotas)
    return (
      <Alert
        type="error"
        showIcon
        message="无法读取当前环境"
        description="权限可能已变化，请重新加载。"
        action={
          <Button onClick={() => setGeneration(value => value + 1)}>
            重试
          </Button>
        }
      />
    );

  const quotaFields: {
    name: keyof Quotas;
    label: string;
    min: number;
    max: number;
  }[] = [
    { name: 'maxConcurrentRuns', label: '同时运行任务数', min: 0, max: 100000 },
    { name: 'maxQueuedRuns', label: '等待队列长度', min: 0, max: 1000000 },
    { name: 'maxSandboxes', label: '同时运行沙盒数', min: 0, max: 100000 },
    {
      name: 'dailyTokenBudget',
      label: '每日 token 预算',
      min: 0,
      max: Number.MAX_SAFE_INTEGER,
    },
    {
      name: 'auditRetentionDays',
      label: '新审计记录保留天数',
      min: 1,
      max: 3650,
    },
  ];
  const quotaPanel = (
    <Form
      form={form}
      layout="vertical"
      disabled={!editable || saving}
      onFinish={(values: Quotas) =>
        void change(() =>
          saveQuotas(tenant.tenantId, environment, {
            ...values,
            revision: quotas.revision,
          })
        )
      }
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
          gap: 20,
        }}
      >
        {quotaFields.map(field => (
          <Form.Item
            key={field.name}
            name={field.name}
            label={field.label}
            rules={[{ required: true, message: '请输入配额' }]}
          >
            <InputNumber
              min={field.min}
              max={field.max}
              precision={0}
              style={{ width: '100%' }}
            />
          </Form.Item>
        ))}
      </div>
      {editable && (
        <Button type="primary" htmlType="submit" loading={saving}>
          保存配额
        </Button>
      )}
    </Form>
  );

  const membersPanel = (
    <>
      {editable && (
        <Button
          type="primary"
          style={{ marginBottom: 16 }}
          onClick={() => {
            setEditing(undefined);
            memberForm.resetFields();
            memberForm.setFieldValue('role', 'developer');
            setMemberOpen(true);
          }}
        >
          添加成员
        </Button>
      )}
      <Table<Member>
        rowKey="uid"
        dataSource={members}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: '用户', dataIndex: 'username' },
          { title: '用户 ID', dataIndex: 'uid', ellipsis: true },
          {
            title: '角色',
            dataIndex: 'role',
            render: (value: TenantRole) => roleNames[value],
          },
          {
            title: '操作',
            render: (_: unknown, member: Member) =>
              editable && (
                <Space>
                  <Button
                    type="link"
                    onClick={() => {
                      setEditing(member);
                      memberForm.setFieldsValue(member);
                      setMemberOpen(true);
                    }}
                  >
                    修改角色
                  </Button>
                  <Popconfirm
                    title="撤销该成员在当前环境的权限？"
                    onConfirm={() =>
                      change(() =>
                        removeMember(tenant.tenantId, environment, member)
                      )
                    }
                  >
                    <Button type="link" danger disabled={saving}>
                      撤销
                    </Button>
                  </Popconfirm>
                </Space>
              ),
          },
        ]}
      />
      <Modal
        title={editing ? '修改角色' : '添加环境成员'}
        open={memberOpen}
        onCancel={() => setMemberOpen(false)}
        confirmLoading={saving}
        onOk={() => memberForm.submit()}
      >
        <Form
          form={memberForm}
          layout="vertical"
          onFinish={async (value: { uid: string; role: TenantRole }) => {
            setSaving(true);
            try {
              await setMember(
                tenant.tenantId,
                environment,
                value.uid,
                value.role,
                editing?.revision ?? null
              );
              setMemberOpen(false);
              setGeneration(value => value + 1);
              message.success('成员权限已保存');
            } catch {
              failure();
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item
            name="uid"
            label="已注册用户 ID"
            rules={[{ required: true }, { max: 128 }]}
          >
            <Input disabled={!!editing} />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={roleOptions} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );

  const auditPanel = (
    <Table<AuditEntry>
      rowKey="id"
      dataSource={audit}
      pagination={{ pageSize: 10 }}
      scroll={{ x: 800 }}
      columns={[
        { title: '时间', dataIndex: 'createdAt' },
        { title: '操作者', dataIndex: 'actorUid', ellipsis: true },
        { title: '操作', dataIndex: 'action' },
        { title: '对象', dataIndex: 'subjectId', ellipsis: true },
      ]}
      expandable={{
        expandedRowRender: entry => (
          <div
            style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}
          >
            <div>
              修改前
              <pre style={{ whiteSpace: 'pre-wrap' }}>
                {entry.beforeValue ?? '—'}
              </pre>
            </div>
            <div>
              修改后
              <pre style={{ whiteSpace: 'pre-wrap' }}>
                {entry.afterValue ?? '—'}
              </pre>
            </div>
          </div>
        ),
      }}
    />
  );

  return (
    <Tabs
      items={[
        { key: 'quota', label: '环境配额', children: quotaPanel },
        {
          key: 'models',
          label: '模型连接',
          children: (
            <ModelConnections
              key={`${tenant.tenantId}:${environment}`}
              tenantId={tenant.tenantId}
              environment={environment}
              role={role}
              active={tenant.status === 'active'}
            />
          ),
        },
        ...(administrator
          ? [
              { key: 'members', label: '成员与角色', children: membersPanel },
              {
                key: 'audit',
                label: '审计记录（最近 100 条）',
                children: auditPanel,
              },
            ]
          : []),
      ]}
    />
  );
}

function TenantWorkspace({
  tenant,
  platformAdmin,
}: {
  tenant: Tenant;
  platformAdmin: boolean;
}): JSX.Element {
  const [environment, setEnvironment] = useState<Environment>('test');
  const [grants, setGrants] = useState<Membership[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [adminUid, setAdminUid] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let ignore = false;
    setGrants(null);
    setFailed(false);
    listMemberships(tenant.tenantId)
      .then(data => {
        if (!ignore) setGrants(data);
      })
      .catch(() => {
        if (!ignore) setFailed(true);
      });
    return () => {
      ignore = true;
    };
  }, [tenant.tenantId, refresh]);
  const membership = grants?.find(grant => grant.environment === environment);
  return (
    <Card
      title={
        <Space>
          <span>{tenant.displayName}</span>
          <Tag color={tenant.status === 'active' ? 'green' : 'default'}>
            {statusNames[tenant.status]}
          </Tag>
        </Space>
      }
      extra={
        <Select
          aria-label="选择环境"
          value={environment}
          onChange={setEnvironment}
          options={[
            { value: 'test', label: '测试环境' },
            { value: 'prod', label: '生产环境' },
          ]}
        />
      }
    >
      {platformAdmin && tenant.status === 'active' && (
        <Space style={{ marginBottom: 20 }} wrap>
          <Input
            aria-label="环境管理员用户 ID"
            placeholder="环境管理员用户 ID"
            value={adminUid}
            onChange={event => setAdminUid(event.target.value)}
            maxLength={128}
          />
          <Button
            loading={saving}
            disabled={!adminUid.trim()}
            onClick={async () => {
              setSaving(true);
              try {
                await setMember(
                  tenant.tenantId,
                  environment,
                  adminUid.trim(),
                  'tenant_admin',
                  null,
                  true
                );
                setAdminUid('');
                setRefresh(value => value + 1);
                message.success('已登记环境管理员');
              } catch {
                failure();
              } finally {
                setSaving(false);
              }
            }}
          >
            登记环境管理员
          </Button>
        </Space>
      )}
      {failed ? (
        <Alert
          type="error"
          message="读取权限失败"
          action={
            <Button onClick={() => setRefresh(value => value + 1)}>重试</Button>
          }
        />
      ) : grants === null ? (
        <Spin />
      ) : membership ? (
        <EnvironmentPanel
          key={`${tenant.tenantId}:${environment}:${refresh}`}
          tenant={tenant}
          environment={environment}
          role={membership.role}
        />
      ) : (
        <Empty description="你尚未获得此环境的访问权限" />
      )}
    </Card>
  );
}

export default function PlatformTenants(): JSX.Element {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [platformAdmin, setPlatformAdmin] = useState(false);
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<{ displayName: string; ownerUid: string }>();
  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setFailed(false);
    listTenants()
      .then(data => {
        if (ignore) return;
        setTenants(data.tenants);
        setPlatformAdmin(data.platformAdmin);
        setSelected(previous =>
          data.tenants.some(tenant => tenant.tenantId === previous)
            ? previous
            : (data.tenants[0]?.tenantId ?? '')
        );
      })
      .catch(() => {
        if (!ignore) setFailed(true);
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [refresh]);
  const tenant = tenants.find(value => value.tenantId === selected);
  return (
    <main
      style={{
        padding: 32,
        overflow: 'auto',
        width: '100%',
        background: '#f6f7fb',
      }}
    >
      <Typography.Title level={2} style={{ marginTop: 0 }}>
        租户管理
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        管理团队成员、测试与生产环境权限，以及任务和沙盒配额。
      </Typography.Paragraph>
      <Space wrap style={{ marginBottom: 24 }}>
        <Select
          aria-label="选择租户"
          style={{ width: 300 }}
          value={selected || undefined}
          onChange={setSelected}
          placeholder="选择租户"
          loading={loading}
          options={tenants.map(value => ({
            value: value.tenantId,
            label: value.displayName,
          }))}
        />
        <Button onClick={() => setRefresh(value => value + 1)}>刷新</Button>
        {platformAdmin && (
          <Button
            type="primary"
            onClick={() => {
              form.resetFields();
              setCreateOpen(true);
            }}
          >
            创建租户
          </Button>
        )}
        {platformAdmin && tenant && tenant.status !== 'archived' && (
          <Popconfirm
            title={
              tenant.status === 'active'
                ? '停用后将拒绝新任务，保留历史记录。继续？'
                : '重新启用该租户？'
            }
            onConfirm={async () => {
              try {
                await setTenantStatus(
                  tenant,
                  tenant.status === 'active' ? 'suspended' : 'active'
                );
                setRefresh(value => value + 1);
              } catch {
                failure();
              }
            }}
          >
            <Button danger={tenant.status === 'active'}>
              {tenant.status === 'active' ? '停用租户' : '启用租户'}
            </Button>
          </Popconfirm>
        )}
      </Space>
      {loading ? (
        <Spin />
      ) : failed ? (
        <Alert type="error" showIcon message="租户列表加载失败" />
      ) : tenant ? (
        <TenantWorkspace
          key={`${tenant.tenantId}:${tenant.revision}`}
          tenant={tenant}
          platformAdmin={platformAdmin}
        />
      ) : (
        <Empty description="暂无可访问的租户" />
      )}
      <Modal
        title="创建企业租户"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={saving}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async value => {
            setSaving(true);
            try {
              const result = await createTenant(
                value.displayName,
                value.ownerUid
              );
              setSelected(result.tenantId);
              setCreateOpen(false);
              setRefresh(previous => previous + 1);
              message.success('租户已创建');
            } catch {
              failure();
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item
            name="displayName"
            label="租户名称"
            rules={[{ required: true, whitespace: true }, { max: 50 }]}
          >
            <Input maxLength={50} />
          </Form.Item>
          <Form.Item
            name="ownerUid"
            label="测试环境管理员用户 ID"
            rules={[{ required: true, whitespace: true }, { max: 128 }]}
          >
            <Input maxLength={128} />
          </Form.Item>
          <Typography.Paragraph type="secondary">
            管理员需先登录注册。生产环境权限在创建后单独授予。
          </Typography.Paragraph>
        </Form>
      </Modal>
    </main>
  );
}

import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { Environment, TenantRole } from '@/services/platform-tenants';
import {
  listModels,
  saveModel,
  probeModel,
  ModelConnection,
  ModelInput,
  ProbeResult,
} from '@/services/platform-models';

export default function ModelConnections({
  tenantId,
  environment,
  role,
  active,
}: {
  tenantId: string;
  environment: Environment;
  role: TenantRole;
  active: boolean;
}): JSX.Element {
  const [models, setModels] = useState<ModelConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [editing, setEditing] = useState<ModelConnection>();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState<string>();
  const [results, setResults] = useState<Record<string, ProbeResult>>({});
  const [form] = Form.useForm<ModelInput>();
  const mounted = useRef(true);
  const editable = active && role === 'tenant_admin';
  const canProbe = active && environment === 'test' && role !== 'viewer';
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      form.resetFields();
    };
  }, [form]);
  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setFailed(false);
    listModels(tenantId, environment)
      .then(rows => {
        if (!ignore) setModels(rows);
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
  }, [tenantId, environment, generation]);
  const edit = (row?: ModelConnection): void => {
    setEditing(row);
    form.resetFields();
    form.setFieldsValue(
      row
        ? {
            displayName: row.display_name,
            endpoint: row.endpoint,
            modelName: row.model_name,
            supportsStream: row.supports_stream,
            supportsTools: row.supports_tools,
            supportsUsage: row.supports_usage,
            connectTimeoutSeconds: row.connect_timeout_seconds,
            readTimeoutSeconds: row.read_timeout_seconds,
            totalTimeoutSeconds: row.total_timeout_seconds,
            caRef: row.ca_ref || undefined,
            expectedVersion: row.current_version,
            revokePrevious: false,
          }
        : {
            supportsStream: true,
            supportsTools: true,
            supportsUsage: true,
            connectTimeoutSeconds: 10,
            readTimeoutSeconds: 90,
            totalTimeoutSeconds: 180,
            expectedVersion: 0,
            revokePrevious: false,
          }
    );
    setOpen(true);
  };
  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary">
        登记模型团队提供的接口。配置按租户和环境隔离，保存后生成新版本；密钥保存后仅显示凭据引用。
      </Typography.Paragraph>
      <Space>
        {editable && (
          <Button type="primary" onClick={() => edit()}>
            添加模型连接
          </Button>
        )}
        <Button onClick={() => setGeneration(value => value + 1)}>刷新</Button>
      </Space>
      {failed && (
        <Alert
          type="error"
          message="模型配置读取失败，请刷新或检查环境权限。"
        />
      )}
      <Table
        rowKey="connection_id"
        dataSource={models}
        loading={loading}
        scroll={{ x: 900 }}
        pagination={{ pageSize: 10 }}
        columns={[
          {
            title: '连接',
            dataIndex: 'display_name',
            render: (value: string, row) => (
              <>
                <strong>{value}</strong>
                <div>
                  <Typography.Text type="secondary">
                    {row.model_name} · v{row.current_version}
                  </Typography.Text>
                </div>
              </>
            ),
          },
          { title: '接口', dataIndex: 'endpoint', ellipsis: true },
          {
            title: '声明能力',
            render: (_, row) => (
              <Space size={0} wrap>
                {row.supports_stream && <Tag color="blue">流式</Tag>}
                {row.supports_tools && <Tag color="purple">工具</Tag>}
                {row.supports_usage && <Tag>用量</Tag>}
              </Space>
            ),
          },
          {
            title: '凭据引用',
            dataIndex: 'credential_ref',
            render: (value: string) => (
              <Typography.Text code>{value.slice(0, 8)}…</Typography.Text>
            ),
          },
          {
            title: '连接检查',
            render: (_, row) => {
              const result = results[row.connection_id];
              return result ? (
                <Space direction="vertical" size={0}>
                  <Tag color={result.ok ? 'green' : 'red'}>
                    {result.ok ? '连接成功' : '检查失败'}
                  </Tag>
                  <small>
                    {result.durationMs} ms ·{' '}
                    {result.ok
                      ? result.usage === 'reported'
                        ? '用量已返回'
                        : '用量未知'
                      : result.error}
                  </small>
                  {result.ok && !result.toolsVerified && (
                    <small>工具调用尚未验证</small>
                  )}
                </Space>
              ) : (
                <Typography.Text type="secondary">未检查</Typography.Text>
              );
            },
          },
          {
            title: '操作',
            render: (_, row) => (
              <Space>
                {editable && (
                  <Button size="small" onClick={() => edit(row)}>
                    编辑 / 轮换凭据
                  </Button>
                )}
                {canProbe && (
                  <Button
                    size="small"
                    loading={probing === row.connection_id}
                    disabled={!!probing}
                    onClick={async () => {
                      setProbing(row.connection_id);
                      try {
                        const result = await probeModel(
                          tenantId,
                          environment,
                          row.connection_id
                        );
                        if (mounted.current)
                          setResults(old => ({
                            ...old,
                            [row.connection_id]: result,
                          }));
                      } catch {
                        if (mounted.current)
                          message.error(
                            '连接检查未完成，请检查权限或服务状态。'
                          );
                      } finally {
                        if (mounted.current) setProbing(undefined);
                      }
                    }}
                  >
                    检查连接
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={editing ? '更新模型连接' : '添加模型连接'}
        open={open}
        confirmLoading={saving}
        onOk={() => form.submit()}
        onCancel={() => {
          setOpen(false);
          form.resetFields();
        }}
        destroyOnClose
        width={680}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async (values: ModelInput) => {
            setSaving(true);
            try {
              await saveModel(tenantId, environment, editing?.connection_id, {
                ...values,
                expectedVersion: editing?.current_version ?? 0,
              });
              if (mounted.current) {
                form.resetFields();
                setOpen(false);
                setGeneration(value => value + 1);
                setResults({});
                message.success('模型连接版本已保存');
              }
            } catch {
              if (mounted.current)
                message.error(
                  '保存失败，请检查地址白名单、环境权限或配置版本。'
                );
            } finally {
              if (mounted.current) setSaving(false);
            }
          }}
        >
          <Form.Item
            name="displayName"
            label="连接名称"
            rules={[{ required: true }]}
          >
            <Input maxLength={120} />
          </Form.Item>
          <Form.Item
            name="endpoint"
            label="OpenAI 兼容接口地址"
            rules={[{ required: true }, { type: 'url' }]}
          >
            <Input
              placeholder="https://model.example.internal/v1"
              maxLength={2048}
            />
          </Form.Item>
          <Form.Item
            name="modelName"
            label="模型名称"
            rules={[{ required: true }]}
          >
            <Input maxLength={255} />
          </Form.Item>
          <Form.Item
            name="apiKey"
            label={editing ? '新密钥（留空保留当前凭据）' : 'API 密钥'}
            rules={[{ required: !editing }]}
          >
            <Input.Password autoComplete="new-password" maxLength={4096} />
          </Form.Item>
          <Space wrap size="large">
            <Form.Item
              name="supportsStream"
              label="流式输出"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="supportsTools"
              label="工具调用"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="supportsUsage"
              label="返回用量"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </Space>
          <Space wrap>
            <Form.Item
              name="connectTimeoutSeconds"
              label="连接超时（秒）"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={60} />
            </Form.Item>
            <Form.Item
              name="readTimeoutSeconds"
              label="读取超时（秒）"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={1800} />
            </Form.Item>
            <Form.Item
              name="totalTimeoutSeconds"
              label="总超时（秒）"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={3600} />
            </Form.Item>
          </Space>
          <Form.Item name="caRef" label="内部 CA 引用（可选，由管理员登记）">
            <Input maxLength={128} />
          </Form.Item>
          {editing && (
            <Form.Item
              name="revokePrevious"
              label="同时吊销此连接的全部旧版本"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </Space>
  );
}

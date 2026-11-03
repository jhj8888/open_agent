import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { Environment, TenantRole } from '@/services/platform-tenants';
import {
  deleteSkillRelease,
  getSkillFile,
  getSkillManifest,
  importLegacySkill,
  listSkillImages,
  listSkillReleases,
  promoteSkillRelease,
  publishSkillRelease,
  SkillManifest,
  SkillRelease,
} from '@/services/platform-skills';

export default function SkillReleases({
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
  const [rows, setRows] = useState<SkillRelease[]>([]);
  const [images, setImages] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [archive, setArchive] = useState<File>();
  const [source, setSource] = useState<'zip' | 'draft'>('zip');
  const [promotion, setPromotion] = useState('');
  const [detail, setDetail] = useState<{
    release: SkillRelease;
    manifest: SkillManifest;
  }>();
  const [form] = Form.useForm<{
    version: string;
    runtimeImage: string;
    skillId?: number;
  }>();
  const mounted = useRef(true);
  const editable =
    active &&
    environment === 'test' &&
    ['tenant_admin', 'developer'].includes(role);
  const canPromote = active && environment === 'prod' && role === 'operator';
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setFailed(false);
    Promise.all([
      listSkillReleases(tenantId, environment),
      listSkillImages(tenantId, environment),
    ])
      .then(([releases, runtimes]) => {
        if (!ignore) {
          setRows(releases);
          setImages(runtimes);
        }
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
  }, [tenantId, environment, refresh]);
  const failure = (): void => {
    if (mounted.current)
      message.error('操作未完成，请查看错误提示并刷新后重试');
  };
  const changed = (): void => {
    if (mounted.current) setRefresh(value => value + 1);
  };
  const inspect = async (release: SkillRelease): Promise<void> => {
    try {
      const manifest = await getSkillManifest(
        tenantId,
        environment,
        release.release_id
      );
      if (mounted.current) setDetail({ release, manifest });
    } catch {
      failure();
    }
  };
  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary">
        发布版本固定
        SKILL.md、脚本、资源和运行镜像。修改草稿后需发布新版本；生产环境使用已通过测试的原版本。
      </Typography.Paragraph>
      <Space wrap>
        <Button loading={loading} onClick={changed}>
          刷新版本
        </Button>
        {editable && (
          <Button
            type="primary"
            disabled={images.length === 0}
            onClick={() => {
              form.resetFields();
              setArchive(undefined);
              setOpen(true);
            }}
          >
            发布 Skill 包
          </Button>
        )}
        {editable && images.length === 0 && !loading && (
          <Typography.Text type="secondary">
            管理员尚未配置可用的运行镜像。
          </Typography.Text>
        )}
      </Space>
      {canPromote && (
        <Space wrap>
          <Input
            aria-label="待推广的发布 ID"
            placeholder="输入测试通过的发布 ID"
            value={promotion}
            onChange={event => setPromotion(event.target.value)}
            style={{ width: 340 }}
            maxLength={36}
          />
          <Button
            disabled={!/^[a-f0-9-]{36}$/.test(promotion)}
            loading={saving}
            onClick={async () => {
              setSaving(true);
              try {
                await promoteSkillRelease(tenantId, promotion);
                if (mounted.current) {
                  message.success('已推广到生产环境');
                  setPromotion('');
                  changed();
                }
              } catch {
                failure();
              } finally {
                if (mounted.current) setSaving(false);
              }
            }}
          >
            推广到生产
          </Button>
        </Space>
      )}
      {failed && <Alert type="error" showIcon message="Skill 版本加载失败" />}
      <Table<SkillRelease>
        loading={loading}
        rowKey="release_id"
        dataSource={rows}
        scroll={{ x: 1100 }}
        pagination={{ pageSize: 10 }}
        columns={[
          {
            title: 'Skill',
            dataIndex: 'skill_name',
            render: (name: string, row) => (
              <>
                <Typography.Text strong>{name}</Typography.Text>
                <div>{row.description}</div>
              </>
            ),
          },
          { title: '版本', dataIndex: 'version_label' },
          {
            title: '文件',
            render: (_, row) =>
              `${row.file_count} 个 · ${(row.total_bytes / 1024).toFixed(1)} KiB`,
          },
          {
            title: '摘要',
            render: (_, row) => (
              <Typography.Text code copyable={{ text: row.manifest_sha256 }}>
                {row.manifest_sha256.slice(0, 12)}…
              </Typography.Text>
            ),
          },
          {
            title: '测试结果',
            render: (_, row) => (
              <Tag color={row.verified ? 'green' : 'default'}>
                {row.verified ? '已通过' : '待验证'}
              </Tag>
            ),
          },
          {
            title: '状态',
            dataIndex: 'status',
            render: (status: SkillRelease['status']) => (
              <Tag>
                {
                  { ready: '可用', revoked: '已撤销', deleted: '已删除' }[
                    status
                  ]
                }
              </Tag>
            ),
          },
          {
            title: '操作',
            render: (_, row) => (
              <Space>
                <Button
                  size="small"
                  disabled={row.status === 'deleted'}
                  onClick={() => void inspect(row)}
                >
                  文件与摘要
                </Button>
                {editable && row.status !== 'deleted' && (
                  <Popconfirm
                    title="删除此发布版本？保留期内或被引用的版本无法删除。"
                    onConfirm={async () => {
                      try {
                        await deleteSkillRelease(
                          tenantId,
                          environment,
                          row.release_id
                        );
                        changed();
                      } catch {
                        failure();
                      }
                    }}
                  >
                    <Button size="small" danger>
                      删除
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title="发布 Skill 包"
        open={open}
        destroyOnClose
        confirmLoading={saving}
        onCancel={() => {
          if (!saving) setOpen(false);
        }}
        onOk={() => form.submit()}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async value => {
            if (source === 'zip' && !archive) {
              message.error('请选择 ZIP 文件');
              return;
            }
            setSaving(true);
            try {
              const result =
                source === 'draft'
                  ? await importLegacySkill(
                      tenantId,
                      environment,
                      value.skillId!,
                      value.version,
                      value.runtimeImage
                    )
                  : await publishSkillRelease(
                      tenantId,
                      environment,
                      archive!,
                      value.version,
                      value.runtimeImage
                    );
              if (mounted.current) {
                message.success(
                  result.reused ? '相同版本已存在，已复用' : 'Skill 版本已发布'
                );
                setOpen(false);
                setArchive(undefined);
                changed();
              }
            } catch {
              failure();
            } finally {
              if (mounted.current) setSaving(false);
            }
          }}
        >
          <Form.Item label="发布来源">
            <Select
              value={source}
              onChange={setSource}
              options={[
                { value: 'zip', label: '上传 ZIP 文件' },
                { value: 'draft', label: '导入现有草稿' },
              ]}
            />
          </Form.Item>
          {source === 'draft' ? (
            <>
              <Form.Item
                name="skillId"
                label="草稿 Skill 文件 ID"
                rules={[{ required: true }]}
              >
                <InputNumber min={1} precision={0} style={{ width: '100%' }} />
              </Form.Item>
              <Typography.Paragraph type="secondary">
                导入当前租户下的 SKILL.md
                及同目录资源。导入期间请暂停编辑；发布后继续编辑草稿不会改变此版本。
              </Typography.Paragraph>
            </>
          ) : (
            <Form.Item label="ZIP 文件（最多 8 MiB）" required>
              <input
                type="file"
                accept=".zip,application/zip"
                aria-label="Skill ZIP 文件"
                onChange={event => {
                  const file = event.target.files?.[0];
                  if (file && file.size > 8 * 1024 * 1024) {
                    message.error('文件超过 8 MiB');
                    event.target.value = '';
                    setArchive(undefined);
                  } else setArchive(file);
                }}
              />
            </Form.Item>
          )}
          <Form.Item
            name="version"
            label="版本名称"
            rules={[
              { required: true },
              {
                pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/,
                message: '请使用字母、数字、点、横线或下划线',
              },
            ]}
          >
            <Input placeholder="例如 v1.0.0" maxLength={64} />
          </Form.Item>
          <Form.Item
            name="runtimeImage"
            label="运行镜像"
            rules={[{ required: true }]}
          >
            <Select options={images.map(value => ({ value, label: value }))} />
          </Form.Item>
          <Typography.Paragraph type="secondary">
            ZIP 根目录需包含带 name、description 的 SKILL.md，也可包含一个同名
            Skill 目录。依赖需预装在选定镜像中。
          </Typography.Paragraph>
        </Form>
      </Modal>
      <Modal
        title={
          detail
            ? `${detail.release.skill_name} · ${detail.release.version_label}`
            : '版本详情'
        }
        open={!!detail}
        width={880}
        footer={null}
        onCancel={() => setDetail(undefined)}
      >
        {detail && (
          <>
            <Typography.Paragraph>
              发布 ID：
              <Typography.Text copyable>
                {detail.release.release_id}
              </Typography.Text>
            </Typography.Paragraph>
            <Typography.Paragraph>
              Manifest SHA-256：
              <Typography.Text copyable>
                {detail.release.manifest_sha256}
              </Typography.Text>
            </Typography.Paragraph>
            <Typography.Paragraph style={{ overflowWrap: 'anywhere' }}>
              运行镜像：{detail.manifest.runtimeImage}
            </Typography.Paragraph>
            <Typography.Paragraph>
              保留至：{detail.release.retain_until}
            </Typography.Paragraph>
            <Table
              dataSource={detail.manifest.files.map((file, index) => ({
                ...file,
                index,
              }))}
              rowKey="path"
              pagination={false}
              columns={[
                { title: '路径', dataIndex: 'path' },
                { title: '字节', dataIndex: 'size' },
                {
                  title: 'SHA-256',
                  render: (_, file) => (
                    <Typography.Text copyable={{ text: file.sha256 }}>
                      {file.sha256.slice(0, 12)}…
                    </Typography.Text>
                  ),
                },
                {
                  title: '下载',
                  render: (_, file) => (
                    <Button
                      size="small"
                      onClick={async () => {
                        try {
                          const blob = await getSkillFile(
                            tenantId,
                            environment,
                            detail.release.release_id,
                            file.index
                          );
                          if (!mounted.current) return;
                          const url = URL.createObjectURL(blob);
                          const link = document.createElement('a');
                          link.href = url;
                          link.download =
                            file.path.split('/').pop() || 'skill-file';
                          link.click();
                          setTimeout(() => URL.revokeObjectURL(url), 1000);
                        } catch {
                          failure();
                        }
                      }}
                    >
                      下载
                    </Button>
                  ),
                },
              ]}
            />
          </>
        )}
      </Modal>
    </Space>
  );
}

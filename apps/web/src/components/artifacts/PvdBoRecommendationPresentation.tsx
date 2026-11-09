import { Alert, Table, Tag, Typography } from "antd";
import type { ReactNode } from "react";

// 贝叶斯模型推荐候选参数（pvd_bayesian_optimization_candidate_generation）
// 用户视角呈现：先说结论（是否生成了推荐、依据多少数据），再给候选参数表
// （分层角色 + 四参数 + 预测值 + 不确定性），最后是训练构成、物理约束与边界
// 提示。技术审计（核参数、池统计等）折叠在"技术核查详情"由通用层承载。

type JsonObject = Record<string, unknown>;

const ROLE_META: Record<string, { label: string; color: string; intent: string }> = {
  高值主峰探索: {
    label: "高值主峰探索",
    color: "success",
    intent: "在模型最有把握的高性能区域内部，兼顾预测值与参数多样性。",
  },
  高潜力多样化: {
    label: "高潜力多样化",
    color: "processing",
    intent: "在性能略低但覆盖面大的区域铺开参数组合，避免整批挤在同一角落。",
  },
  远域探索: {
    label: "远域探索",
    color: "cyan",
    intent: "远离已有实验的区域取样，检验模型支持边界、发现潜在更优区间。",
  },
  exploration_exploitation_tradeoff_with_diversity: {
    label: "探索与利用均衡",
    color: "processing",
    intent: "按探索-利用均衡与批次多样性贪心选择。",
  },
};

const APPLICABILITY_LABELS: Record<string, { label: string; tone: "success" | "warning" | "error" | "info" }> = {
  eligible: { label: "当前实验体系与模型匹配", tone: "success" },
  indeterminate: { label: "实验体系信息不完整", tone: "warning" },
  ineligible: { label: "当前实验体系与模型不匹配", tone: "error" },
};

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}
function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.map(asObject).filter((row): row is JsonObject => Boolean(row))
    : [];
}
function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
function numberText(value: unknown, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";
}
function intText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? String(Math.round(value))
    : "—";
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-5" data-testid={`pvd-bo-section-${title}`}>
      <Typography.Text strong className="text-[15px]">
        {title}
      </Typography.Text>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function ParameterCell({ row }: { row: JsonObject }) {
  // 产物契约中四参数嵌套在 candidate.parameters 下（顶层无平铺副本）
  const parameters = asObject(row.parameters) ?? row;
  return (
    <span className="tabular-nums">
      {intText(parameters.power)} W · N₂ {intText(parameters.n2_flow)} · Ar{" "}
      {intText(parameters.ar_flow)} · {intText(parameters.deposition_time)} s
    </span>
  );
}

export function PvdBoRecommendationPresentation({ document }: { document: JsonObject }) {
  const status = text(document.candidate_generation_status);
  const applicability = asObject(document.applicability);
  const applicabilityKey = text(applicability?.status);
  const candidates = asObjectArray(document.candidates);
  const training = asObject(document.training_summary);
  const objective = asObject(document.objective);
  const diagnostics = asObject(document.run_diagnostics);
  const provenance = asObject(document.provenance);

  if (status !== "generated") {
    const tone = APPLICABILITY_LABELS[applicabilityKey]?.tone ?? "warning";
    const issues = (Array.isArray(applicability?.issues)
      ? applicability.issues
      : []
    ).filter((s): s is string => typeof s === "string");
    return (
      <div className="grid gap-3" data-testid="pvd-bo-not-applicable">
        <Alert
          type={tone === "error" ? "error" : tone === "success" ? "success" : "warning"}
          message="本轮未生成贝叶斯候选参数"
          description={
            applicabilityKey === "ineligible"
              ? "当前任务的实验体系（靶材/衬底/靶基距等）与已冻结的贝叶斯模型定义域不一致，为避免不可信推荐，本轮未训练模型、未输出候选。候选环节以“不适用”如实记录，不影响后续可行性核验与推荐节点。"
              : "实验体系信息尚不完整，暂时无法确认与贝叶斯模型定义域一致；待体系信息补齐后，下一轮反馈将自动生成候选。"
          }
          showIcon
        />
        {issues.length > 0 ? (
          <div className="rounded-lg border border-border bg-surface px-4 py-3">
            <Typography.Text type="secondary" className="text-[13px]">
              差异项：{issues.join("；")}
            </Typography.Text>
          </div>
        ) : null}
      </div>
    );
  }

  const baseCount = typeof training?.base_count === "number" ? training.base_count : 0;
  const projectCount = typeof training?.project_count === "number" ? training.project_count : 0;
  const totalCount = typeof training?.total_count === "number" ? training.total_count : baseCount + projectCount;
  const targetMetric = text(objective?.target_metric) || "目标指标";
  const excludedObserved =
    typeof diagnostics?.excluded_observed_count === "number"
      ? diagnostics.excluded_observed_count
      : null;
  const constrainedGrid =
    typeof diagnostics?.constrained_grid_count === "number"
      ? diagnostics.constrained_grid_count
      : null;
  const profileId = text(provenance?.profile_id);

  const roleCounts = new Map<string, number>();
  for (const candidate of candidates) {
    const role = text(candidate.selection_role);
    roleCounts.set(role, (roleCounts.get(role) ?? 0) + 1);
  }

  const tableColumns = [
    {
      title: "优先级",
      dataIndex: "candidate_rank",
      key: "rank",
      width: 72,
      render: (value: unknown) => `#${intText(value)}`,
    },
    {
      title: "分层角色",
      dataIndex: "selection_role",
      key: "role",
      width: 130,
      render: (value: unknown) => {
        const meta = ROLE_META[text(value)];
        return meta ? (
          <Tag color={meta.color}>{meta.label}</Tag>
        ) : (
          <Tag>{text(value) || "—"}</Tag>
        );
      },
    },
    {
      title: "工艺参数",
      key: "parameters",
      render: (_: unknown, row: JsonObject) => <ParameterCell row={row} />,
    },
    {
      title: `预测${targetMetric}`,
      dataIndex: "predicted_target",
      key: "predicted",
      align: "right" as const,
      render: (value: unknown) => numberText(value),
    },
    {
      title: "模型不确定性",
      dataIndex: "predictive_std_latent",
      key: "uncertainty",
      align: "right" as const,
      render: (value: unknown) => `± ${numberText(value)}`,
    },
    {
      title: "推荐得分",
      dataIndex: "acquisition_score",
      key: "score",
      align: "right" as const,
      render: (value: unknown) => numberText(value),
    },
  ];

  return (
    <div className="grid gap-1" data-testid="pvd-bo-recommendation">
      <Alert
        type="success"
        showIcon
        message={`已生成 ${candidates.length} 组候选工艺参数`}
        description={`基于 ${totalCount} 条同体系实验数据（基线数据集 ${baseCount} 条${projectCount > 0 ? ` + 本项目真实反馈 ${projectCount} 条` : ""}）训练高斯过程代理模型，按“高值主峰探索 / 高潜力多样化 / 远域探索”三层策略推荐。候选为模型预测结果，需经可行性核验后进入最终推荐。`}
      />

      <Section title="候选参数">
        <Table
          size="small"
          rowKey={(row: JsonObject) => intText(row.candidate_rank)}
          columns={tableColumns}
          dataSource={candidates}
          pagination={false}
        />
      </Section>

      {roleCounts.size > 0 ? (
        <Section title="分层意图">
          <div className="grid gap-2">
            {[...roleCounts.entries()].map(([role, count]) => {
              const meta = ROLE_META[role];
              return (
                <div
                  key={role}
                  className="flex items-start gap-2 rounded-lg border border-border bg-surface px-4 py-2"
                >
                  {meta ? (
                    <Tag color={meta.color} className="mt-0.5">
                      {meta.label} ×{count}
                    </Tag>
                  ) : (
                    <Tag className="mt-0.5">
                      {role} ×{count}
                    </Tag>
                  )}
                  <Typography.Text type="secondary" className="text-[13px]">
                    {meta?.intent ?? ""}
                  </Typography.Text>
                </div>
              );
            })}
          </div>
        </Section>
      ) : null}

      <Section title="训练与适用性">
        <div className="grid gap-1 rounded-lg border border-border bg-surface px-4 py-3 text-[13px] text-text-secondary">
          <div>
            训练数据：基线数据集 {baseCount} 条
            {projectCount > 0 ? `，本项目同体系真实反馈 ${projectCount} 条` : "（本轮尚无项目真实反馈，全部来自历史基线）"}
            ；仅采用“已执行且有效”的实验与最终有效测量。本次为现场重训（非预存模型文件），
            训练集拟合优度（决定系数）{numberText(diagnostics?.train_r2)}、平均绝对误差 {numberText(diagnostics?.train_mae)}
            （拟合诊断，不代表外推精度）。
          </div>
          {constrainedGrid !== null ? (
            <div>
              候选空间：在设备参数范围内枚举 {constrainedGrid.toLocaleString()} 个满足物理约束
              （总流量与氮气占比）的组合
              {excludedObserved !== null ? `，剔除已实验过的 ${excludedObserved} 个` : ""}
              ，再按推荐得分与批次多样性选出上表候选。
            </div>
          ) : null}
          <div>
            体系匹配：当前实验体系与冻结模型档案{profileId ? `（${profileId}）` : ""}的定义域一致，
            跨体系历史数据不会进入训练。
          </div>
        </div>
      </Section>

      <Section title="边界提示">
        <Typography.Text type="secondary" className="text-[13px]">
          候选参数是统计模型的推荐，不是实验事实：预测值与不确定性来自历史数据的外推，
          每组候选仍须通过“参数与实验方案可行性核验”（设备能力、硬约束、资源）后，
          才会进入本轮最终实验推荐。
        </Typography.Text>
      </Section>
    </div>
  );
}

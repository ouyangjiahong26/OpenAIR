import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import {
  ScientificArtifactPresentation,
  scientificArtifactPresentationKind,
} from "../../ScientificArtifactPresentation";
import fixtures from "./pvdBoRecommendationFixtures.json";

// 夹具为真实技能输出（同一科学状态输入重放 + 体系不匹配示例），不是手写形状。

function boArtifact(content: unknown): WorkbenchArtifact {
  return {
    id: "artifact-bo",
    type: "bo_candidates",
    artifactType: "bo_candidates",
    title: "贝叶斯模型推荐候选参数",
    format: "json",
    relatedStage: "execution_validation" as never,
    content: JSON.stringify(content),
  };
}

describe("PVD 贝叶斯模型推荐候选参数呈现", () => {
  afterEach(() => {
    cleanup();
  });

  it("routes bo_candidates artifacts to the dedicated presentation", () => {
    expect(scientificArtifactPresentationKind(boArtifact(fixtures.generated))).toBe(
      "pvd_bo_recommendation",
    );
  });

  it("leads with the conclusion and shows the tiered candidate table", () => {
    render(<ScientificArtifactPresentation artifact={boArtifact(fixtures.generated)} />);
    expect(screen.getByText("已生成 10 组候选工艺参数")).toBeTruthy();
    expect(screen.getByText(/基于 63 条同体系实验数据/)).toBeTruthy();
    expect(screen.getAllByText("高值主峰探索").length).toBeGreaterThanOrEqual(4);
    expect(screen.getAllByText("高潜力多样化").length).toBeGreaterThanOrEqual(4);
    expect(screen.getAllByText("远域探索").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("#1")).toBeTruthy();
    // 工艺参数取自 candidate.parameters 嵌套对象（首行夹具值），不能渲染成"—"。
    expect(screen.getByText(/255 W · N₂ 12 · Ar 27 · 1500 s/)).toBeTruthy();
    expect(screen.queryByText(/— W · N₂ —/)).toBeNull();
    expect(screen.getByText(/剔除已实验过的/)).toBeTruthy();
    expect(screen.getByText(/本次为现场重训/)).toBeTruthy();
    expect(screen.getByText(/决定系数/)).toBeTruthy();
    expect(screen.getByText(/候选参数是统计模型的推荐，不是实验事实/)).toBeTruthy();
    // 用户视角不出现技术缩写。
    expect(screen.queryByText(/\bUCB\b|\bGP\b|\bBO\b|latent|R²/)).toBeNull();
  });

  it("explains a not-applicable run instead of showing empty fields", () => {
    render(<ScientificArtifactPresentation artifact={boArtifact(fixtures.notApplicable)} />);
    expect(screen.getByText("本轮未生成贝叶斯候选参数")).toBeTruthy();
    expect(screen.getByText(/与已冻结的贝叶斯模型定义域不一致/)).toBeTruthy();
    expect(screen.getByText(/process_context_mismatch:substrate/)).toBeTruthy();
    expect(screen.queryByText("未提供")).toBeNull();
  });
});

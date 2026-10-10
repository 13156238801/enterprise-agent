<script setup lang="ts">
const sections = ['工作台', '审批', '合同', '任务追踪']
</script>

<template>
  <div class="app-shell">
    <aside
      class="sidebar"
      aria-label="主导航"
    >
      <a
        class="brand"
        href="#main"
        aria-label="Enterprise Agent 首页"
      >
        <span
          class="brand-mark"
          aria-hidden="true"
        >EA</span>
        <span>Enterprise Agent</span>
      </a>
      <nav>
        <a
          v-for="(section, index) in sections"
          :key="section"
          href="#main"
          :aria-current="index === 0 ? 'page' : undefined"
        >
          <span
            class="nav-dot"
            aria-hidden="true"
          />{{ section }}
        </a>
      </nav>
      <p class="sidebar-foot">
        本地演示环境
      </p>
    </aside>

    <main
      id="main"
      class="main-content"
    >
      <header class="topbar">
        <p>
          Agent 工作台
        </p>
        <span class="environment"><span aria-hidden="true" />开发环境</span>
      </header>

      <section
        class="welcome"
        aria-labelledby="welcome-title"
      >
        <p class="eyebrow">
          ENTERPRISE AGENT PLATFORM
        </p>
        <h1 id="welcome-title">
          让业务工作流更清晰
        </h1>
        <p class="intro">
          在这里开始使用企业 Agent。当前项目骨架已就绪，业务能力将在后续阶段逐步接入。
        </p>
        <div class="starter-card">
          <div
            class="starter-icon"
            aria-hidden="true"
          >
            ✦
          </div>
          <div>
            <h2>
              开始新的任务
            </h2>
            <p>
              对话与任务执行入口将在后续阶段开放。
            </p>
          </div>
          <button
            type="button"
            disabled
            aria-label="新建任务，后续阶段开放"
          >
            即将开放
          </button>
        </div>
      </section>
    </main>
  </div>
</template>

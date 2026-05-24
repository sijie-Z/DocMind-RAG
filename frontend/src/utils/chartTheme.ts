import * as echarts from 'echarts'

export const CHART_COLORS = {
  primary: '#64748b',
  secondary: '#10b981',
  warning: '#f59e0b',
  danger: '#ef4444',
  info: '#3b82f6',
  sky: '#0ea5e9',
  cyan: '#06b6d4',
  pink: '#ec4899',
}

export const CHART_THEME = {
  color: [CHART_COLORS.primary, CHART_COLORS.secondary, CHART_COLORS.sky, CHART_COLORS.cyan, CHART_COLORS.warning],
  backgroundColor: 'transparent',
  textStyle: {
    color: '#6b7280',
  },
  title: {
    textStyle: {
      color: '#111827',
      fontWeight: 600,
    },
    subtextStyle: {
      color: '#6b7280',
    },
  },
  line: {
    smooth: true,
    symbol: 'circle',
    symbolSize: 6,
    lineStyle: {
      width: 2,
    },
    areaStyle: {
      opacity: 0.1,
    },
  },
  bar: {
    barBorderRadius: [4, 4, 0, 0],
    itemStyle: {
      borderRadius: 4,
    },
  },
  pie: {
    radius: ['40%', '70%'],
    itemStyle: {
      borderRadius: 4,
      borderColor: '#fff',
      borderWidth: 2,
    },
    label: {
      color: '#6b7280',
    },
  },
  grid: {
    left: '3%',
    right: '4%',
    bottom: '3%',
    top: '10%',
    containLabel: true,
  },
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(255, 255, 255, 0.95)',
    borderColor: '#e5e7eb',
    borderWidth: 1,
    textStyle: {
      color: '#374151',
    },
    axisPointer: {
      type: 'cross',
      crossStyle: {
        color: '#999',
      },
    },
  },
  legend: {
    textStyle: {
      color: '#6b7280',
    },
  },
  xAxis: {
    axisLine: {
      lineStyle: {
        color: '#e5e7eb',
      },
    },
    axisLabel: {
      color: '#6b7280',
    },
    splitLine: {
      lineStyle: {
        color: '#f3f4f6',
      },
    },
  },
  yAxis: {
    axisLine: {
      lineStyle: {
        color: '#e5e7eb',
      },
    },
    axisLabel: {
      color: '#6b7280',
    },
    splitLine: {
      lineStyle: {
        color: '#f3f4f6',
      },
    },
  },
}

export const DARK_CHART_THEME = {
  color: [CHART_COLORS.primary, CHART_COLORS.secondary, CHART_COLORS.sky, CHART_COLORS.cyan, CHART_COLORS.warning],
  backgroundColor: 'transparent',
  textStyle: {
    color: '#9ca3af',
  },
  title: {
    textStyle: {
      color: '#f3f4f6',
      fontWeight: 600,
    },
    subtextStyle: {
      color: '#9ca3af',
    },
  },
  line: CHART_THEME.line,
  bar: CHART_THEME.bar,
  pie: {
    ...CHART_THEME.pie,
    itemStyle: {
      borderRadius: 4,
      borderColor: '#1f2937',
      borderWidth: 2,
    },
    label: {
      color: '#9ca3af',
    },
  },
  grid: CHART_THEME.grid,
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(31, 41, 55, 0.95)',
    borderColor: '#374151',
    borderWidth: 1,
    textStyle: {
      color: '#f3f4f6',
    },
    axisPointer: {
      type: 'cross',
      crossStyle: {
        color: '#6b7280',
      },
    },
  },
  legend: {
    textStyle: {
      color: '#9ca3af',
    },
  },
  xAxis: {
    axisLine: {
      lineStyle: {
        color: '#374151',
      },
    },
    axisLabel: {
      color: '#9ca3af',
    },
    splitLine: {
      lineStyle: {
        color: '#1f2937',
      },
    },
  },
  yAxis: {
    axisLine: {
      lineStyle: {
        color: '#374151',
      },
    },
    axisLabel: {
      color: '#9ca3af',
    },
    splitLine: {
      lineStyle: {
        color: '#1f2937',
      },
    },
  },
}

export function createChartOption(baseOption: Record<string, unknown>, isDark = false): Record<string, unknown> {
  const theme = isDark ? DARK_CHART_THEME : CHART_THEME
  return { ...theme, ...baseOption }
}

export function initChartWithTheme(dom: HTMLElement, isDark = false): echarts.ECharts {
  const chart = echarts.init(dom, isDark ? 'dark' : undefined)
  return chart
}

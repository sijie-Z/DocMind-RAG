import * as echarts from 'echarts'

export const CHART_COLORS = {
  primary: '#6366f1',
  secondary: '#10b981',
  warning: '#f59e0b',
  danger: '#ef4444',
  info: '#3b82f6',
  purple: '#a855f7',
  cyan: '#06b6d4',
  pink: '#ec4899',
}

export const CHART_THEME = {
  color: [CHART_COLORS.primary, CHART_COLORS.secondary, CHART_COLORS.purple, CHART_COLORS.cyan, CHART_COLORS.warning],
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

export function createChartOption(baseOption: Record<string, any>): Record<string, any> {
  return { ...CHART_THEME, ...baseOption }
}

export function initChartWithTheme(dom: HTMLElement): echarts.ECharts {
  const chart = echarts.init(dom)
  return chart
}

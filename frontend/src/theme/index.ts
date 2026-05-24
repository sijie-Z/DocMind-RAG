import type { GlobalThemeOverrides } from 'naive-ui'

const sharedCommon = {
  primaryColor: '#64748b',
  primaryColorHover: '#475569',
  primaryColorPressed: '#334155',
  primaryColorSuppl: '#94a3b8',
  infoColor: '#3b82f6',
  successColor: '#10b981',
  warningColor: '#f59e0b',
  errorColor: '#ef4444',
  borderRadius: '10px',
  borderRadiusSmall: '6px',
  fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  fontSize: '14px',
  fontWeightStrong: '600',
}

export const lightThemeOverrides: GlobalThemeOverrides = {
  common: sharedCommon,
  Card: {
    borderRadius: '14px',
    borderColor: 'rgba(226, 232, 240, 1)',
    boxShadow: '0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.06)',
    paddingMedium: '20px',
  },
  Button: {
    borderRadiusMedium: '8px',
    borderRadiusLarge: '10px',
    borderRadiusSmall: '6px',
    fontWeight: '600',
  },
  Input: {
    borderRadius: '8px',
    border: '1px solid #e2e8f0',
    borderHover: '1px solid #cbd5e1',
    borderFocus: '1px solid #64748b',
    boxShadowFocus: '0 0 0 3px rgba(100, 116, 139, 0.12)',
    placeholderColor: 'rgba(148, 163, 184, 1)',
    paddingMedium: '8px 12px',
  },
  Popover: {
    borderRadius: '10px',
    padding: '12px',
    boxShadow: '0 4px 24px rgba(0, 0, 0, 0.08)',
  },
  Dropdown: {
    borderRadius: '10px',
    optionHeightMedium: '36px',
    padding: '4px',
    boxShadow: '0 4px 24px rgba(0, 0, 0, 0.08)',
  },
  Menu: {
    borderRadius: '8px',
    itemHeightMedium: '40px',
    itemBorderRadius: '8px',
  },
  Modal: {
    borderRadius: '16px',
    boxShadow: '0 24px 48px rgba(0, 0, 0, 0.12)',
  },
  Dialog: {
    borderRadius: '16px',
    padding: '24px',
    iconSize: '32px',
  },
  Message: {
    borderRadius: '10px',
    padding: '12px 20px',
    boxShadow: '0 4px 12px rgba(0, 0, 0, 0.06)',
  },
  Tag: {
    borderRadius: '6px',
  },
}

export const darkThemeOverrides: GlobalThemeOverrides = {
  common: {
    ...sharedCommon,
    primaryColor: '#94a3b8',
    primaryColorHover: '#64748b',
    primaryColorPressed: '#475569',
    primaryColorSuppl: '#cbd5e1',
    bodyColor: '#0a0a0f',
    cardColor: '#111118',
    modalColor: '#111118',
    popoverColor: '#16161f',
    tableColor: '#111118',
    inputColor: '#111118',
    borderColor: 'rgba(255, 255, 255, 0.06)',
    dividerColor: 'rgba(255, 255, 255, 0.06)',
  },
  Card: {
    borderColor: 'rgba(255, 255, 255, 0.06)',
    boxShadow: '0 1px 3px rgba(0, 0, 0, 0.3), 0 1px 2px rgba(0, 0, 0, 0.2)',
  },
  Input: {
    color: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderHover: '1px solid rgba(255, 255, 255, 0.14)',
    borderFocus: '1px solid rgba(148, 163, 184, 0.5)',
    textColor: '#f1f5f9',
    placeholderColor: 'rgba(148, 163, 184, 0.6)',
    borderRadius: '8px',
    boxShadowFocus: '0 0 0 3px rgba(148, 163, 184, 0.2)',
  },
  Popover: {
    boxShadow: '0 4px 24px rgba(0, 0, 0, 0.4)',
  },
}

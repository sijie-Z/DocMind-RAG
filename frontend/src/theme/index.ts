import type { GlobalThemeOverrides } from 'naive-ui'

export const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#10a37f', // ChatGPT Green
    primaryColorHover: '#0e906f',
    primaryColorPressed: '#0c7b5e',
    primaryColorSuppl: '#10a37f',
    infoColor: '#2080f0',
    successColor: '#18a058',
    warningColor: '#f0a020',
    errorColor: '#d03050',
    borderRadius: '12px',
    borderRadiusSmall: '6px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
  },
  Card: {
    borderRadius: '16px',
    borderColor: 'rgba(239, 239, 245, 1)'
  },
  Button: {
    borderRadiusMedium: '10px',
    borderRadiusLarge: '12px',
    borderRadiusSmall: '8px',
    fontWeight: '600'
  },
  Input: {
    borderRadius: '10px',
    boxShadowFocus: '0 0 0 2px rgba(16, 163, 127, 0.2)'
  },
  Popover: {
    borderRadius: '12px',
    padding: '12px',
    boxShadow: '0 8px 30px rgba(0, 0, 0, 0.12)'
  },
  Dropdown: {
    borderRadius: '12px',
    optionHeightMedium: '36px',
    padding: '6px'
  },
  Menu: {
    borderRadius: '10px',
    itemHeightMedium: '42px',
    itemBorderRadius: '10px'
  },
  Modal: {
    borderRadius: '20px',
    boxShadow: '0 20px 50px rgba(0, 0, 0, 0.15)'
  },
  Dialog: {
    borderRadius: '20px',
    padding: '24px',
    iconSize: '32px'
  },
  Message: {
    borderRadius: '10px',
    padding: '12px 20px',
    boxShadow: '0 4px 12px rgba(0, 0, 0, 0.08)'
  }
}

export const darkThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#10a37f',
    primaryColorHover: '#0e906f',
    primaryColorPressed: '#0c7b5e'
  },
  Card: {
    borderColor: 'rgba(255, 255, 255, 0.09)'
  },
  Input: {
    color: 'rgba(255, 255, 255, 0.1)',
    textColor: '#ffffff',
    placeholderColor: 'rgba(255, 255, 255, 0.3)',
    borderRadius: '10px',
    boxShadowFocus: '0 0 0 2px rgba(16, 163, 127, 0.2)'
  },
  Popover: {
    boxShadow: '0 8px 30px rgba(0, 0, 0, 0.3)'
  }
}

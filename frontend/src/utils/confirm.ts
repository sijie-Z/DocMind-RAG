import { useDialog } from 'naive-ui'
import { useI18n } from 'vue-i18n'

/**
 * 破坏性操作统一二次确认（E2）。
 *
 * 口径与 profile 页设备下线确认一致：Naive UI `dialog.warning` + Promise<boolean>；
 * 只有点「确认」resolve(true)，取消 / 关闭弹窗一律 resolve(false)——调用方在
 * false 时不得触碰接口与状态（取消零副作用）。
 *
 * 用法（setup 内取一次，返回可复用函数）：
 *   const confirmDangerousAction = useConfirmDangerousAction()
 *   if (!(await confirmDangerousAction(title, content))) return
 */
export function useConfirmDangerousAction() {
  const dialog = useDialog()
  const { t } = useI18n()
  return (title: string, content: string): Promise<boolean> =>
    new Promise((resolve) => {
      dialog.warning({
        title,
        content,
        positiveText: t('common.confirm'),
        negativeText: t('common.cancel'),
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onClose: () => resolve(false),
      })
    })
}

import * as Dialog from '@radix-ui/react-dialog'
import { useApp } from '../store'

// Raven asks "are you sure" only for permanent delete and lossy steps (section 12.1).
export default function ConfirmDialog() {
  const c = useApp((s) => s.confirm)
  const close = (): void => useApp.setState({ confirm: null })
  return (
    <Dialog.Root open={!!c} onOpenChange={(o) => !o && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="anim-fade fixed inset-0 bg-black/45 backdrop-blur-md" />
        <Dialog.Content
          className="fixed left-1/2 top-[30%] w-[440px] -translate-x-1/2 float-panel anim-pop p-6"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && c) {
              e.preventDefault()
              c.run()
              close()
            }
          }}
        >
          <Dialog.Title className={`serif mb-3 text-[18px] leading-none ${c?.danger ? 'text-danger' : ''}`}>{c?.title}</Dialog.Title>
          <Dialog.Description className="mb-6">{c?.body}</Dialog.Description>
          <div className="flex items-center justify-end gap-5">
            <Dialog.Close className="link text-muted hover:text-text">Cancel (Esc)</Dialog.Close>
            <button
              autoFocus
              className="px-5 py-1.5 font-medium text-white transition-[filter] hover:brightness-110"
              style={{ borderRadius: 2, background: c?.danger ? 'var(--danger)' : 'var(--accent)' }}
              onClick={() => {
                c?.run()
                close()
              }}
            >
              {c?.action} (Enter)
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

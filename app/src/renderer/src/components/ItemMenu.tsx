import * as CM from '@radix-ui/react-context-menu'
import { Archive, ArrowRightLeft, ClipboardPaste, Copy, ExternalLink, FilePlus, FolderOpen, FolderPlus, FolderTree, Info, Link2, PenLine, PieChart, ListChecks, ScanSearch, ScrollText, Scissors, Star, Tags, Terminal, Trash2, Undo2, Image as ImageIcon, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'
import { showInSpace } from '../screens/SpaceScreen'
import {
  ARCHIVE,
  inTrash,
  invertSelection,
  selectAll,
  POLICY_NAMES,
  loadPolicies,
  makeLink,
  shredSelection,
  togglePolicy,
  copyPathToClipboard,
  copyRelativePaths,
  createArchive,
  extractArchive,
  openWithApp,
  convertFiles,
  convertTargets,
  copySelection,
  type ConvertTarget,
  createItem,
  deleteSelection,
  newTab,
  openEntry,
  openFilesTab,
  paste,
  reload,
  restoreFromTrash,
  nameFiles,
  say,
  selectedPaths,
  setPref,
  summarize,
  toggleFavorite,
  trashSelection,
  useApp,
  visibleEntries
} from '../store'

const itemCls = 'flex cursor-default items-center justify-between gap-4 mx-1.5 rounded-md px-2.5 py-[5px] outline-none transition-colors duration-75 data-[highlighted]:bg-hover data-[disabled]:opacity-40'

// A small icon in front of each action, so a long menu can be scanned by shape.
const ICON: [RegExp, LucideIcon][] = [
  [/^Open in new tab/, ExternalLink],
  [/^Open terminal/, Terminal],
  [/^Open with/, ExternalLink],
  [/^Open$/, FolderOpen],
  [/^Show in Space/, PieChart],
  [/^Show in system/, FolderOpen],
  [/^Summarize/, ScrollText],
  [/^Find similar/, ScanSearch],
  [/^Cut/, Scissors],
  [/^Copy (relative )?paths?$/, Link2],
  [/^Copy/, Copy],
  [/^Paste/, ClipboardPaste],
  [/^Select all/, ListChecks],
  [/^Extract/, Archive],
  [/^Rename/, PenLine],
  [/^New folder/, FolderPlus],
  [/^New file/, FilePlus],
  [/favorites$/, Star],
  [/^Properties/, Info],
  [/^Move to trash/, Trash2],
  [/^Delete permanently/, Trash2],
  [/^Restore/, Undo2],
  [/^One PDF/, ImageIcon],
  [/^Organize/, FolderTree],
  [/^Set category/, Tags],
  [/^Convert/, ArrowRightLeft]
]

function Item({ label, keys, onSelect, danger, disabled }: { label: string; keys?: string; onSelect: () => void; danger?: boolean; disabled?: boolean }) {
  const Icon = ICON.find(([re]) => re.test(label))?.[1]
  return (
    <CM.Item className={itemCls} onSelect={onSelect} disabled={disabled}>
      <span className={`flex items-center gap-2.5 ${danger ? 'text-danger' : ''}`}>
        {Icon ? <Icon size={14} strokeWidth={1.6} className={danger ? '' : 'text-muted'} /> : <span className="w-[14px]" />}
        {label}
      </span>
      {keys && <kbd className="kbd !h-5 !border-b !text-[10.5px]">{keys}</kbd>}
    </CM.Item>
  )
}

const Sep = () => <CM.Separator className="mx-3 my-1.5 h-px bg-line" />

/** The context menu shows only items that apply to the selection (section 12.4). */
const IMAGE = /\.(png|jpe?g|webp|heic|avif|jxl|tiff?|bmp|gif)$/i
const LOOKS = /\.(png|jpe?g|webp|heic|avif|jxl|tiff?|bmp|gif|pdf|mp4|mkv|webm|mov|avi|m4v|3gp)$/i

/** Folder rules (PLAN2 P2-5): four switches for one folder. */
function PolicyMenu({ path }: { path: string }) {
  const policies = useApp((s) => s.policies)
  useEffect(() => void loadPolicies(), [])
  const own = policies.find((p) => p.path === path)
  return (
    <CM.Sub>
      <CM.SubTrigger className={itemCls}>
        <span>Folder rules</span>
        <span className="mono text-[11px] text-muted">{own ? '•' : '›'}</span>
      </CM.SubTrigger>
      <CM.Portal>
        <CM.SubContent className="z-50 min-w-[220px] float-panel anim-pop py-1.5">
          {POLICY_NAMES.map(([k, label]) => (
            <Item key={k} label={`${own?.[k] ? '✓ ' : '   '}${label}`} onSelect={() => void togglePolicy(path, k)} />
          ))}
        </CM.SubContent>
      </CM.Portal>
    </CM.Sub>
  )
}

function ConvertMenu({ paths }: { paths: string[] }) {
  const [targets, setTargets] = useState<ConvertTarget[] | null>(null)
  const key = paths.join('\n')
  useEffect(() => {
    let live = true
    setTargets(null)
    void convertTargets(paths).then((t) => live && setTargets(t))
    return () => {
      live = false
    }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const images = paths.length > 1 && paths.every((p) => IMAGE.test(p))
  if (targets !== null && !targets.length && !images) return null
  return (
    <CM.Sub>
      <CM.SubTrigger className={itemCls}>
        <span>Convert to…</span>
        <span className="mono text-[11px] text-muted">›</span>
      </CM.SubTrigger>
      <CM.Portal>
        <CM.SubContent className="z-50 min-w-[200px] float-panel anim-pop py-1.5">
          {targets === null && <div className="px-3 py-[3px] text-muted">…</div>}
          {images && <Item label="One PDF from these images" onSelect={() => void convertFiles(paths, 'pdf', true)} />}
          {targets?.map((t) => (
            <Item key={t.ext} label={t.label} keys={t.tool ?? ''} onSelect={() => void convertFiles(paths, t.ext)} />
          ))}
        </CM.SubContent>
      </CM.Portal>
    </CM.Sub>
  )
}

let categoryList: string[] | null = null

/** Set category (learn.py): the correction becomes an example for files like this one. */
function CategoryMenu({ path }: { path: string }) {
  const [cats, setCats] = useState<string[] | null>(categoryList)
  useEffect(() => {
    if (!categoryList) void get<{ categories: string[] }>('/classify/categories').then((r) => setCats((categoryList = r.categories)))
  }, [])
  const set = async (category: string): Promise<void> => {
    try {
      const r = await post<{ followed: number; learned: boolean }>('/classify/set', { path, category })
      say(
        `Category set to ${category}.${r.learned ? ' Raven uses this file as an example for files like it.' : ''}${r.followed ? ` ${r.followed} similar files follow it.` : ''}`
      )
      void reload()
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }
  return (
    <CM.Sub>
      <CM.SubTrigger className={itemCls}>
        <span>Set category…</span>
        <span className="mono text-[11px] text-muted">›</span>
      </CM.SubTrigger>
      <CM.Portal>
        <CM.SubContent className="z-50 max-h-[60vh] min-w-[200px] overflow-y-auto float-panel anim-pop py-1.5">
          {!cats && <div className="px-3 py-[3px] text-muted">…</div>}
          {cats?.map((c) => <Item key={c} label={c} onSelect={() => void set(c)} />)}
        </CM.SubContent>
      </CM.Portal>
    </CM.Sub>
  )
}

export function ItemMenuContent() {
  useApp((s) => s.selected) // re-render when the selection changes
  const paths = selectedPaths()
  const clipboard = useApp.getState().clipboard
  const current = useApp.getState().path
  const favorites = useApp.getState().favorites
  const entries = visibleEntries().filter((e) => paths.includes(e.path))
  const one = entries.length === 1 ? entries[0] : null
  const none = entries.length === 0
  const folderTarget = one?.is_dir ? one.path : current
  // Inside an archive Raven only reads: no rename, delete, paste or new items.
  const readOnly = useApp.getState().listing?.writable === false
  return (
    <CM.Portal>
      <CM.Content className="z-50 min-w-[240px] float-panel anim-pop py-1.5">
        {!none && inTrash(current) && (
          <>
            <Item label="Restore" onSelect={() => void restoreFromTrash(paths)} />
            <Sep />
          </>
        )}
        {!none && <Item label="Open" keys="Enter" onSelect={() => entries.forEach((e) => void openEntry(e))} />}
        {one?.is_dir && <Item label="Open in new tab" keys="Middle-click" onSelect={() => openFilesTab(one.path)} />}
        {one && !one.is_dir && <Item label="Open with default app" onSelect={() => void openWithApp(one.path)} />}
        {!none && <Item label="Show in system file manager" onSelect={() => void window.raven?.showInFolder(entries[0].path)} />}
        <Item label="Open terminal here" onSelect={() => void window.raven?.openTerminal(folderTarget)} />
        <Item label="Show in Space" onSelect={() => void showInSpace(folderTarget)} />
        <Item label="Summarize folder" onSelect={() => void summarize(folderTarget)} />
        {one && !one.is_dir && LOOKS.test(one.name) && (
          <Item label="Find similar" onSelect={() => newTab('results', { cmdQuery: `similar:${one.path}`, results: null })} />
        )}
        <Sep />
        {!none && <Item label="Cut" keys="Ctrl+X" onSelect={() => copySelection('cut')} />}
        {!none && <Item label="Copy" keys="Ctrl+C" onSelect={() => copySelection('copy')} />}
        <Item label={one?.is_dir ? 'Paste into folder' : 'Paste'} keys="Ctrl+V" disabled={!clipboard || readOnly} onSelect={() => void paste(folderTarget)} />
        <Item label="Select all" keys="Ctrl+A" onSelect={selectAll} />
        {!none && <Item label="Invert selection" keys="Ctrl+Shift+I" onSelect={invertSelection} />}
        <Sep />
        {!none && !readOnly && entries.every((e) => !e.is_dir) && <ConvertMenu paths={paths} />}
        {one && !one.is_dir && ARCHIVE.test(one.name) && !readOnly && <Item label="Extract here" onSelect={() => void extractArchive(one.path)} />}
        {!none && !readOnly && (
          <CM.Sub>
            <CM.SubTrigger className={itemCls}>
              <span>Create archive…</span>
              <span className="mono text-[11px] text-muted">›</span>
            </CM.SubTrigger>
            <CM.Portal>
              <CM.SubContent className="z-50 min-w-[200px] float-panel anim-pop py-1.5">
                {(['zip', '7z', 'tar.zst', 'tar.gz'] as const).map((f) => (
                  <Item key={f} label={`.${f}`} keys={f === 'zip' ? 'opens anywhere' : f === 'tar.zst' ? 'fast, small' : ''} onSelect={() => void createArchive(paths, f)} />
                ))}
              </CM.SubContent>
            </CM.Portal>
          </CM.Sub>
        )}
        {one && !one.is_dir && !readOnly && <CategoryMenu path={one.path} />}
        {one && !readOnly && <Item label="Rename" keys="F2" onSelect={() => useApp.setState({ renaming: one.path })} />}
        {one && !one.is_dir && !readOnly && <Item label="Suggest a name" onSelect={() => void nameFiles(one.path)} />}
        {(none || one?.is_dir) && !readOnly && <Item label="Name unnamed files" onSelect={() => void nameFiles(folderTarget)} />}
        {entries.length > 1 && !readOnly && <Item label={`Rename ${entries.length} items…`} keys="F2" onSelect={() => useApp.setState({ batchRename: paths })} />}
        {!readOnly && <Item label="New folder" keys="Ctrl+Shift+N" onSelect={() => void createItem('mkdir')} />}
        {!readOnly && <Item label="New file" onSelect={() => void createItem('newfile')} />}
        <Sep />
        <Item label={paths.length > 1 ? 'Copy paths' : 'Copy path'} onSelect={() => void copyPathToClipboard(none ? [current] : paths)} />
        {!none && <Item label="Copy relative path" keys="from the project" onSelect={() => void copyRelativePaths(paths)} />}
        {one?.is_dir && (
          <Item label={favorites.includes(one.path) ? 'Remove from favorites' : 'Add to favorites'} onSelect={() => toggleFavorite(one.path)} />
        )}
        {!none && <Item label="Properties" keys="Space" onSelect={() => setPref('inspector', true)} />}
        {one && !readOnly && (
          <CM.Sub>
            <CM.SubTrigger className={itemCls}>
              <span>Make link</span>
              <span className="mono text-[11px] text-muted">›</span>
            </CM.SubTrigger>
            <CM.Portal>
              <CM.SubContent className="z-50 min-w-[180px] float-panel anim-pop py-1.5">
                <Item label="Symbolic link" onSelect={() => void makeLink(one.path, 'symbolic')} />
                {!one.is_dir && <Item label="Hard link" keys="same disk" onSelect={() => void makeLink(one.path, 'hard')} />}
              </CM.SubContent>
            </CM.Portal>
          </CM.Sub>
        )}
        {(none || one?.is_dir) && (
          <Item label="Find broken links" onSelect={() => newTab('results', { cmdQuery: `is:broken in:${folderTarget.slice(folderTarget.lastIndexOf('/') + 1)}`, results: null })} />
        )}
        {(none || one?.is_dir) && <PolicyMenu path={folderTarget} />}
        {!none && !readOnly && (
          <>
            <Sep />
            {!inTrash(current) && <Item label="Move to trash" keys="Delete" onSelect={() => void trashSelection()} />}
            <Item label="Delete permanently" keys={inTrash(current) ? 'Delete' : 'Shift+Delete'} danger onSelect={deleteSelection} />
            {entries.every((e) => !e.is_link) && <Item label="Shred…" danger onSelect={shredSelection} />}
          </>
        )}
      </CM.Content>
    </CM.Portal>
  )
}

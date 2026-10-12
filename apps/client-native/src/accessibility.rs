//! UI-thread Windows adapter and bounded UI action routing. No Agent authority lives here.
use crate::app::Message;
use iced::{advanced::widget::Id, Rectangle};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex, OnceLock},
};
#[derive(Clone, Copy, Debug)]
pub enum Role {
    Button,
    CheckBox,
    TextInput,
    TextEditor,
    ComboBox,
    Label,
}
#[derive(Clone)]
pub struct Semantic {
    pub id: u64,
    pub key: Id,
    pub label: String,
    pub role: Role,
    pub enabled: bool,
    pub value: Option<String>,
    pub checked: Option<bool>,
}
#[derive(Clone)]
enum Action {
    Press(Message),
    Value(Arc<dyn Fn(String) -> Option<Message> + Send + Sync>),
}
struct Entry {
    semantic: Semantic,
    action: Option<Action>,
    bounds: Option<Rectangle>,
    focused: bool,
    clipped: bool,
}
struct Registry {
    epoch: u32,
    next: u64,
    title: String,
    counts: BTreeMap<String, u32>,
    entries: BTreeMap<u64, Entry>,
    ids: BTreeMap<String, u64>,
    order: Vec<u64>,
    bounds: Rectangle,
    scale: f64,
}
impl Default for Registry {
    fn default() -> Self {
        Self {
            epoch: 0,
            next: 2,
            title: "RACP".into(),
            counts: BTreeMap::new(),
            entries: BTreeMap::new(),
            ids: BTreeMap::new(),
            order: vec![],
            bounds: Rectangle::default(),
            scale: 1.0,
        }
    }
}
fn registry() -> &'static Mutex<Registry> {
    static VALUE: OnceLock<Mutex<Registry>> = OnceLock::new();
    VALUE.get_or_init(|| Mutex::new(Registry::default()))
}
pub fn begin(title: String) {
    if let Ok(mut value) = registry().lock() {
        value.epoch = value
            .epoch
            .checked_add(1)
            .expect("UI accessibility epoch exhausted");
        let active: std::collections::BTreeSet<u64> = value.entries.keys().copied().collect();
        value.ids.retain(|_, id| active.contains(id));
        value.title = title;
        value.counts.clear();
        value.entries.clear();
        value.order.clear();
    }
}
pub fn control(
    label: String,
    role: Role,
    enabled: bool,
    value: Option<String>,
    checked: Option<bool>,
    press: Option<Message>,
    input: Option<Arc<dyn Fn(String) -> Option<Message> + Send + Sync>>,
) -> Semantic {
    let mut state = registry().lock().expect("UI accessibility registry");
    let count = state.counts.entry(label.clone()).or_default();
    *count += 1;
    let identity = format!("racp-control-{role:?}-{label}-{count}");
    let key = Id::new(identity.clone());
    let id = if let Some(id) = state.ids.get(&identity) {
        *id
    } else {
        let id = state.next;
        state.next = state
            .next
            .checked_add(1)
            .expect("UI accessibility ID exhausted");
        state.ids.insert(identity, id);
        id
    };
    state.order.push(id);
    let semantic = Semantic {
        id,
        key,
        label,
        role,
        enabled,
        value,
        checked,
    };
    let action = press
        .map(Action::Press)
        .or_else(|| input.map(Action::Value));
    state.entries.insert(
        id,
        Entry {
            semantic: semantic.clone(),
            action,
            bounds: None,
            focused: false,
            clipped: true,
        },
    );
    semantic
}
pub fn geometry(semantic: &Semantic, bounds: Rectangle, focused: bool, viewport: Rectangle) {
    if let Ok(mut state) = registry().lock() {
        if let Some(entry) = state.entries.get_mut(&semantic.id) {
            entry.bounds = Some(bounds);
            entry.focused = focused;
            entry.clipped = bounds.intersection(&viewport).is_none();
        }
    }
}
pub fn surface(bounds: Rectangle) {
    if let Ok(mut state) = registry().lock() {
        state.bounds = bounds;
    }
}
/// Read scroll translations through Iced's operation API, rather than treating
/// the renderer's content coordinates as window coordinates.
pub struct Geometry {
    translation: iced::Vector,
    viewport: Rectangle,
    pending: Option<(Option<Id>, Rectangle, iced::Vector)>,
}
impl Geometry {
    pub fn new(viewport: Rectangle) -> Self {
        Self {
            translation: iced::Vector::ZERO,
            viewport,
            pending: None,
        }
    }
}
impl iced::advanced::widget::Operation for Geometry {
    fn container(
        &mut self,
        id: Option<&Id>,
        bounds: Rectangle,
        children: &mut dyn FnMut(&mut dyn iced::advanced::widget::Operation),
    ) {
        if self
            .pending
            .as_ref()
            .is_some_and(|(target, area, _)| target.as_ref() == id && *area == bounds)
        {
            let (_, area, translation) = self.pending.take().unwrap();
            let old_translation = self.translation;
            let old_viewport = self.viewport;
            self.viewport = (area - old_translation)
                .intersection(&old_viewport)
                .unwrap_or_default();
            self.translation = self.translation + translation;
            children(self);
            self.translation = old_translation;
            self.viewport = old_viewport;
        } else {
            children(self);
        }
    }
    fn scrollable(
        &mut self,
        _: &mut dyn iced::advanced::widget::operation::Scrollable,
        id: Option<&Id>,
        bounds: Rectangle,
        _: Rectangle,
        translation: iced::Vector,
    ) {
        self.pending = Some((id.cloned(), bounds, translation));
    }
    fn custom(&mut self, value: &mut dyn std::any::Any, _: Option<&Id>) {
        if let Some(location) = value.downcast_ref::<crate::controls::FocusLocation>() {
            geometry(
                &location.semantic,
                location.bounds - self.translation,
                location.focused,
                self.viewport,
            );
        }
    }
}
#[derive(Clone, Debug)]
pub struct Request {
    pub epoch: u32,
    pub target: u64,
    pub operation: Operation,
}
#[derive(Clone, Debug)]
pub enum Operation {
    Focus,
    Press,
    SetValue(String),
}
pub enum Effect {
    Focus(Id),
    Message(Message),
}
pub fn resolve(request: Request) -> Option<Effect> {
    let state = registry().lock().ok()?;
    if request.epoch != state.epoch && !matches!(request.operation, Operation::Focus) {
        return None;
    }
    let entry = state.entries.get(&request.target)?;
    if !entry.semantic.enabled {
        return None;
    }
    match (request.operation, entry.action.as_ref()) {
        (Operation::Focus, _) => Some(Effect::Focus(entry.semantic.key.clone())),
        (Operation::Press, Some(Action::Press(message))) => Some(Effect::Message(message.clone())),
        (Operation::SetValue(value), Some(Action::Value(handler))) if value.len() <= 65536 => {
            handler(value).map(Effect::Message)
        }
        _ => None,
    }
}
struct Queue {
    sender: tokio::sync::mpsc::Sender<Request>,
    receiver: tokio::sync::Mutex<Option<tokio::sync::mpsc::Receiver<Request>>>,
}
fn queue() -> &'static Queue {
    static VALUE: OnceLock<Queue> = OnceLock::new();
    VALUE.get_or_init(|| {
        let (sender, receiver) = tokio::sync::mpsc::channel(32);
        Queue {
            sender,
            receiver: tokio::sync::Mutex::new(Some(receiver)),
        }
    })
}
pub async fn events(mut output: iced::futures::channel::mpsc::Sender<Message>) {
    use iced::futures::SinkExt;
    let Some(mut receiver) = queue().receiver.lock().await.take() else {
        std::future::pending::<()>().await;
        return;
    };
    while let Some(request) = receiver.recv().await {
        if output.send(Message::Accessible(request)).await.is_err() {
            break;
        }
    }
}
#[cfg(windows)]
mod windows {
    use super::*;
    use accesskit::{
        Action as NativeAction, ActionData, ActionHandler, ActivationHandler, Node, NodeId,
        Toggled, TreeId, TreeInfo, TreeUpdate,
    };
    use accesskit_windows::{SubclassingAdapter, HWND};
    use std::cell::RefCell;
    thread_local! {static ADAPTER:RefCell<Option<(SubclassingAdapter,HWND)>>=const{RefCell::new(None)};}
    struct Activate;
    impl ActivationHandler for Activate {
        fn request_initial_tree(&mut self) -> Option<TreeUpdate> {
            Some(tree())
        }
    }
    struct Handle;
    impl ActionHandler for Handle {
        fn do_action(&mut self, request: accesskit::ActionRequest) {
            let operation = match (request.action, request.data) {
                (NativeAction::Focus, _) => Operation::Focus,
                (NativeAction::Click, _) => Operation::Press,
                (NativeAction::SetValue, Some(ActionData::Value(value))) => {
                    Operation::SetValue(value.to_string())
                }
                _ => return,
            };
            let Some(epoch) = registry().lock().ok().map(|state| state.epoch) else {
                return;
            };
            let _ = queue().sender.try_send(Request {
                epoch,
                target: request.target_node.0,
                operation,
            });
        }
    }
    fn tree() -> TreeUpdate {
        let state = registry().lock().expect("UI accessibility registry");
        let mut root = Node::new(accesskit::Role::Window);
        root.set_label(state.title.clone());
        root.set_transform(accesskit::Affine::scale(state.scale));
        root.set_bounds(accesskit::Rect::new(
            0.0,
            0.0,
            state.bounds.width as f64,
            state.bounds.height as f64,
        ));
        let mut nodes = vec![];
        let mut children = vec![];
        let mut focus = NodeId(1);
        for entry_id in &state.order {
            let entry = &state.entries[entry_id];
            let semantic = &entry.semantic;
            let mut node = Node::new(match semantic.role {
                Role::Button => accesskit::Role::Button,
                Role::CheckBox => accesskit::Role::CheckBox,
                Role::TextInput => accesskit::Role::TextInput,
                Role::TextEditor => accesskit::Role::MultilineTextInput,
                Role::ComboBox => accesskit::Role::ComboBox,
                Role::Label => accesskit::Role::Label,
            });
            node.set_label(semantic.label.clone());
            if matches!(semantic.role, Role::Label) {
                // UIA exposes a static text node's Name from its value.
                node.set_value(semantic.label.clone());
            }
            if !semantic.enabled && !matches!(semantic.role, Role::Label) {
                node.set_disabled();
            } else if semantic.enabled && !matches!(semantic.role, Role::Label) {
                node.add_action(NativeAction::Focus);
            }
            if semantic.enabled {
                match &entry.action {
                    Some(Action::Press(_)) => node.add_action(NativeAction::Click),
                    Some(Action::Value(_)) => node.add_action(NativeAction::SetValue),
                    None => (),
                }
            }
            if let Some(value) = &semantic.value {
                node.set_value(value.clone());
            }
            if let Some(value) = semantic.checked {
                node.set_toggled(if value { Toggled::True } else { Toggled::False });
            }
            if let Some(bounds) = entry.bounds.filter(|_| !entry.clipped) {
                node.set_bounds(accesskit::Rect::new(
                    bounds.x as f64,
                    bounds.y as f64,
                    (bounds.x + bounds.width) as f64,
                    (bounds.y + bounds.height) as f64,
                ));
            } else {
                node.set_bounds(accesskit::Rect::new(0.0, 0.0, 0.0, 0.0));
            }
            let id = NodeId(*entry_id);
            if entry.focused {
                focus = id;
            }
            children.push(id);
            nodes.push((id, node));
        }
        root.set_children(children);
        nodes.push((NodeId(1), root));
        TreeUpdate {
            nodes,
            tree: Some(TreeInfo::new(NodeId(1))),
            tree_id: TreeId::ROOT,
            focus,
        }
    }
    pub fn attach(handle: raw_window_handle::WindowHandle<'_>) -> Result<(), String> {
        use raw_window_handle::RawWindowHandle;
        let RawWindowHandle::Win32(handle) = handle.as_raw() else {
            return Err("ACCESSIBILITY_WINDOW_UNSUPPORTED".into());
        };
        let hwnd = HWND(handle.hwnd.get() as *mut _);
        update_scale(hwnd);
        ADAPTER.with(|cell| {
            if cell.borrow().is_some() {
                return Err("ACCESSIBILITY_ALREADY_ATTACHED".into());
            }
            let adapter =
                std::panic::catch_unwind(|| SubclassingAdapter::new(hwnd, Activate, Handle))
                    .map_err(|_| "ACCESSIBILITY_ATTACH_FAILED".to_owned())?;
            *cell.borrow_mut() = Some((adapter, hwnd));
            Ok(())
        })
    }
    pub fn publish() {
        ADAPTER.with(|cell| {
            if let Some((adapter, hwnd)) = &mut *cell.borrow_mut() {
                update_scale(*hwnd);
                let events = adapter.update_if_active(tree);
                if let Some(events) = events {
                    events.raise();
                }
            }
        });
    }
    fn update_scale(hwnd: HWND) {
        let dpi = unsafe { windows_sys::Win32::UI::HiDpi::GetDpiForWindow(hwnd.0) };
        if dpi != 0 {
            if let Ok(mut state) = registry().lock() {
                state.scale = f64::from(dpi) / 96.0;
            }
        }
    }
}
#[cfg(windows)]
pub use windows::attach;
pub fn publish() {
    #[cfg(windows)]
    windows::publish();
}

//! Keyboard focus and semantic wrappers around the maintained Iced widgets.
use crate::{
    accessibility::{self, Role, Semantic},
    app::Message,
};
use iced::{
    advanced::{
        self, layout, mouse, overlay, renderer,
        widget::{self, tree, Operation, Tree},
        Clipboard, Layout, Shell, Widget,
    },
    event,
    keyboard::{self, key::Named},
    Element, Event, Length, Rectangle, Renderer, Size, Theme, Vector,
};
#[derive(Default)]
struct State {
    focused: bool,
    armed: Option<Message>,
    id: Option<widget::Id>,
}
impl widget::operation::focusable::Focusable for State {
    fn is_focused(&self) -> bool {
        self.focused
    }
    fn focus(&mut self) {
        self.focused = true;
    }
    fn unfocus(&mut self) {
        self.focused = false;
        self.armed = None;
    }
}
struct Accessible<'a> {
    child: Element<'a, Message>,
    semantic: Semantic,
    press: Option<Message>,
    choices: Option<Choices>,
}
struct Choices {
    values: Vec<String>,
    selected: Option<usize>,
    select: std::sync::Arc<dyn Fn(String) -> Message + Send + Sync>,
}
impl<'a> Accessible<'a> {
    fn element(
        child: Element<'a, Message>,
        semantic: Semantic,
        press: Option<Message>,
    ) -> Element<'a, Message> {
        Self {
            child,
            semantic,
            press,
            choices: None,
        }
        .into()
    }
    fn focused(&self, tree: &Tree) -> bool {
        match self.semantic.role {
            Role::TextInput => tree.children[0].state
                .downcast_ref::<iced::widget::text_input::State<<Renderer as advanced::text::Renderer>::Paragraph>>()
                .is_focused(),
            Role::TextEditor => tree.children[0].state
                .downcast_ref::<iced::widget::text_editor::State<advanced::text::highlighter::PlainText>>()
                .is_focused(),
            _ => tree.state.downcast_ref::<State>().focused,
        }
    }
}
struct FocusKey<'a> {
    operation: &'a mut dyn Operation,
    id: &'a widget::Id,
}
impl Operation for FocusKey<'_> {
    fn container(
        &mut self,
        id: Option<&widget::Id>,
        bounds: Rectangle,
        children: &mut dyn FnMut(&mut dyn Operation),
    ) {
        self.operation.container(id, bounds, &mut |operation| {
            children(&mut FocusKey {
                operation,
                id: self.id,
            });
        });
    }
    fn focusable(&mut self, state: &mut dyn widget::operation::Focusable, _: Option<&widget::Id>) {
        self.operation.focusable(state, Some(self.id));
    }
    fn text_input(&mut self, state: &mut dyn widget::operation::TextInput, _: Option<&widget::Id>) {
        self.operation.text_input(state, Some(self.id));
    }
}
pub(crate) struct FocusLocation {
    pub semantic: Semantic,
    pub bounds: Rectangle,
    pub focused: bool,
}
impl<'a> Widget<Message, Theme, Renderer> for Accessible<'a> {
    fn tag(&self) -> tree::Tag {
        tree::Tag::of::<State>()
    }
    fn state(&self) -> tree::State {
        tree::State::new(State {
            id: Some(self.semantic.key.clone()),
            ..State::default()
        })
    }
    fn children(&self) -> Vec<Tree> {
        vec![Tree::new(&self.child)]
    }
    fn diff(&self, tree: &mut Tree) {
        let state = tree.state.downcast_mut::<State>();
        if state.id.as_ref() != Some(&self.semantic.key) {
            *state = State {
                id: Some(self.semantic.key.clone()),
                ..State::default()
            };
            tree.children[0] = Tree::new(&self.child);
        }
        tree.diff_children(std::slice::from_ref(&self.child));
        if !self.semantic.enabled {
            let state = tree.state.downcast_mut::<State>();
            state.focused = false;
            state.armed = None;
        }
    }
    fn size(&self) -> Size<Length> {
        self.child.as_widget().size()
    }
    fn layout(
        &self,
        tree: &mut Tree,
        renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        self.child
            .as_widget()
            .layout(&mut tree.children[0], renderer, limits)
    }
    fn operate(
        &self,
        tree: &mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        operation: &mut dyn Operation,
    ) {
        if self.semantic.enabled
            && matches!(
                self.semantic.role,
                Role::Button | Role::CheckBox | Role::ComboBox
            )
        {
            operation.focusable(tree.state.downcast_mut::<State>(), Some(&self.semantic.key));
        }
        if matches!(self.semantic.role, Role::TextInput | Role::TextEditor) {
            self.child.as_widget().operate(
                &mut tree.children[0],
                layout,
                renderer,
                &mut FocusKey {
                    operation,
                    id: &self.semantic.key,
                },
            );
        } else {
            self.child
                .as_widget()
                .operate(&mut tree.children[0], layout, renderer, operation);
        }
        operation.custom(
            &mut FocusLocation {
                semantic: self.semantic.clone(),
                bounds: layout.bounds(),
                focused: self.focused(tree),
            },
            Some(&self.semantic.key),
        );
    }
    fn on_event(
        &mut self,
        tree: &mut Tree,
        event: Event,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        renderer: &Renderer,
        clipboard: &mut dyn Clipboard,
        shell: &mut Shell<'_, Message>,
        viewport: &Rectangle,
    ) -> event::Status {
        let state = tree.state.downcast_mut::<State>();
        if matches!(
            event,
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left))
        ) {
            state.focused = self.semantic.enabled && cursor.is_over(layout.bounds());
            state.armed = None;
        }
        if self.press.is_some() && state.focused {
            match &event {
                Event::Keyboard(keyboard::Event::KeyPressed {
                    key: keyboard::Key::Named(Named::Enter | Named::Space),
                    ..
                }) => {
                    if state.armed.is_none() {
                        state.armed = self.press.clone();
                    }
                    return event::Status::Captured;
                }
                Event::Keyboard(keyboard::Event::KeyReleased {
                    key: keyboard::Key::Named(Named::Enter | Named::Space),
                    ..
                }) => {
                    if let Some(message) = state.armed.take() {
                        shell.publish(message);
                    }
                    return event::Status::Captured;
                }
                Event::Keyboard(keyboard::Event::KeyPressed {
                    key: keyboard::Key::Named(Named::Escape),
                    ..
                }) => state.armed = None,
                _ => (),
            }
        }
        if state.focused {
            if let (
                Some(choices),
                Event::Keyboard(keyboard::Event::KeyPressed {
                    key: keyboard::Key::Named(key),
                    modifiers,
                    ..
                }),
            ) = (&self.choices, &event)
            {
                if !modifiers.command() && !modifiers.alt() && !choices.values.is_empty() {
                    let current = choices.selected.unwrap_or(0);
                    let next = match key {
                        Named::ArrowDown | Named::ArrowRight => {
                            Some((current + 1).min(choices.values.len() - 1))
                        }
                        Named::ArrowUp | Named::ArrowLeft => Some(current.saturating_sub(1)),
                        Named::Home => Some(0),
                        Named::End => Some(choices.values.len() - 1),
                        _ => None,
                    };
                    if let Some(next) = next {
                        shell.publish((choices.select)(choices.values[next].clone()));
                        return event::Status::Captured;
                    }
                }
            }
        }
        if matches!(event, Event::Window(iced::window::Event::Unfocused)) {
            state.armed = None;
        }
        self.child.as_widget_mut().on_event(
            &mut tree.children[0],
            event,
            layout,
            cursor,
            renderer,
            clipboard,
            shell,
            viewport,
        )
    }
    fn draw(
        &self,
        tree: &Tree,
        renderer: &mut Renderer,
        theme: &Theme,
        style: &renderer::Style,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
    ) {
        self.child.as_widget().draw(
            &tree.children[0],
            renderer,
            theme,
            style,
            layout,
            cursor,
            viewport,
        );
        let focused = self.focused(tree);
        if focused
            && self.semantic.enabled
            && !matches!(self.semantic.role, Role::TextInput | Role::TextEditor)
        {
            use advanced::Renderer as _;
            renderer.fill_quad(
                renderer::Quad {
                    bounds: layout.bounds(),
                    border: iced::Border {
                        color: theme.palette().primary,
                        width: 2.0,
                        radius: 4.0.into(),
                    },
                    ..Default::default()
                },
                iced::Color::TRANSPARENT,
            );
        }
    }
    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
        self.child.as_widget().mouse_interaction(
            &tree.children[0],
            layout,
            cursor,
            viewport,
            renderer,
        )
    }
    fn overlay<'b>(
        &'b mut self,
        tree: &'b mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        translation: Vector,
    ) -> Option<overlay::Element<'b, Message, Theme, Renderer>> {
        self.child
            .as_widget_mut()
            .overlay(&mut tree.children[0], layout, renderer, translation)
    }
}
impl<'a> From<Accessible<'a>> for Element<'a, Message> {
    fn from(value: Accessible<'a>) -> Self {
        Element::new(value)
    }
}
pub struct Button<'a> {
    inner: iced::widget::Button<'a, Message>,
    label: String,
    press: Option<Message>,
}
pub fn button<'a>(label: impl Into<String>) -> Button<'a> {
    let label = label.into();
    Button {
        inner: iced::widget::button(iced::widget::text(label.clone())),
        label,
        press: None,
    }
}
impl<'a> Button<'a> {
    pub fn on_press(mut self, message: Message) -> Self {
        self.inner = self.inner.on_press(message.clone());
        self.press = Some(message);
        self
    }
    pub fn on_press_maybe(mut self, message: Option<Message>) -> Self {
        self.inner = self.inner.on_press_maybe(message.clone());
        self.press = message;
        self
    }
    pub fn width(mut self, value: impl Into<Length>) -> Self {
        self.inner = self.inner.width(value);
        self
    }
    pub fn accessibility_label(mut self, label: impl Into<String>) -> Self {
        self.label = label.into();
        self
    }
}
impl<'a> From<Button<'a>> for Element<'a, Message> {
    fn from(value: Button<'a>) -> Self {
        let semantic = accessibility::control(
            value.label,
            Role::Button,
            value.press.is_some(),
            None,
            None,
            value.press.clone(),
            None,
        );
        Accessible::element(value.inner.into(), semantic, value.press)
    }
}
pub struct Checkbox<'a> {
    inner: iced::widget::Checkbox<'a, Message>,
    label: String,
    checked: bool,
    press: Option<Message>,
}
pub fn checkbox<'a>(label: impl Into<String>, checked: bool) -> Checkbox<'a> {
    let label = label.into();
    Checkbox {
        inner: iced::widget::checkbox(label.clone(), checked),
        label,
        checked,
        press: None,
    }
}
impl<'a> Checkbox<'a> {
    pub fn on_toggle(mut self, handler: impl Fn(bool) -> Message + 'a) -> Self {
        self.press = Some(handler(!self.checked));
        self.inner = self.inner.on_toggle(handler);
        self
    }
}
impl<'a> From<Checkbox<'a>> for Element<'a, Message> {
    fn from(value: Checkbox<'a>) -> Self {
        let semantic = accessibility::control(
            value.label,
            Role::CheckBox,
            value.press.is_some(),
            None,
            Some(value.checked),
            value.press.clone(),
            None,
        );
        Accessible::element(value.inner.into(), semantic, value.press)
    }
}
pub struct Text<'a> {
    inner: iced::widget::Text<'a>,
    label: String,
}
pub fn text<'a>(value: impl ToString) -> Text<'a> {
    let label = value.to_string();
    Text {
        inner: iced::widget::text(label.clone()),
        label,
    }
}
impl Text<'_> {
    pub fn size(mut self, size: impl Into<iced::Pixels>) -> Self {
        self.inner = self.inner.size(size);
        self
    }
    pub fn width(mut self, width: impl Into<Length>) -> Self {
        self.inner = self.inner.width(width);
        self
    }
}
impl<'a> From<Text<'a>> for Element<'a, Message> {
    fn from(value: Text<'a>) -> Self {
        let semantic =
            accessibility::control(value.label, Role::Label, false, None, None, None, None);
        Accessible::element(value.inner.into(), semantic, None)
    }
}
pub struct TextInput<'a> {
    inner: iced::widget::TextInput<'a, Message>,
    label: String,
    value: String,
    input: Option<std::sync::Arc<dyn Fn(String) -> Option<Message> + Send + Sync>>,
}
pub fn text_input<'a>(label: &str, value: &str) -> TextInput<'a> {
    TextInput {
        inner: iced::widget::text_input(label, value),
        label: label.into(),
        value: value.into(),
        input: None,
    }
}
impl<'a> TextInput<'a> {
    pub fn on_input(mut self, handler: impl Fn(String) -> Message + Send + Sync + 'static) -> Self {
        let handler = std::sync::Arc::new(handler);
        let input = handler.clone();
        self.inner = self.inner.on_input(move |value| handler(value));
        self.input = Some(std::sync::Arc::new(move |value| Some(input(value))));
        self
    }
}
impl<'a> From<TextInput<'a>> for Element<'a, Message> {
    fn from(value: TextInput<'a>) -> Self {
        let semantic = accessibility::control(
            value.label,
            Role::TextInput,
            value.input.is_some(),
            Some(value.value),
            None,
            None,
            value.input,
        );
        // FocusKey supplies the same stable ID to the native text input state.
        Accessible::element(value.inner.into(), semantic, None)
    }
}
pub fn constraint_editor<'a>(
    label: &str,
    content: &'a iced::widget::text_editor::Content,
    field: crate::constraints::Field,
    height: f32,
) -> Element<'a, Message> {
    let input_field = field.clone();
    let semantic = accessibility::control(
        label.into(),
        Role::TextEditor,
        true,
        Some(content.text()),
        None,
        None,
        Some(std::sync::Arc::new(move |value| {
            Some(Message::EditConstraint(input_field.clone(), value))
        })),
    );
    let editor = iced::widget::text_editor(content)
        .height(height)
        .on_action(move |action| Message::EditConstraintList(field.clone(), action));
    Accessible::element(editor.into(), semantic, None)
}
pub fn profile_picklist(selected: &str) -> Element<'_, Message> {
    const VALUES: [&str; 3] = ["read_only", "standard", "trusted_personal"];
    let select: std::sync::Arc<dyn Fn(String) -> Option<Message> + Send + Sync> =
        std::sync::Arc::new(|value| {
            VALUES
                .contains(&value.as_str())
                .then_some(Message::Profile(value))
        });
    let semantic = accessibility::control(
        "실행 프로필 (방향키로 선택)".into(),
        Role::ComboBox,
        true,
        Some(selected.into()),
        None,
        None,
        Some(select),
    );
    let child = iced::widget::pick_list(VALUES, Some(selected), |value| {
        Message::Profile(value.to_owned())
    });
    Element::new(Accessible {
        child: child.into(),
        semantic,
        press: None,
        choices: Some(Choices {
            values: VALUES.iter().map(|v| (*v).into()).collect(),
            selected: VALUES.iter().position(|v| *v == selected),
            select: std::sync::Arc::new(Message::Profile),
        }),
    })
}
pub fn scrollable<'a>(
    content: impl Into<Element<'a, Message>>,
) -> iced::widget::Scrollable<'a, Message> {
    iced::widget::scrollable(content).id(iced::widget::scrollable::Id::new("racp-page-scroll"))
}
/// Run all passes of Iced's focus operation before measuring the focused
/// widget and scrolling its containing page. A Task batch does not express this order.
struct FocusThenReveal {
    inner: Box<dyn Operation<Message>>,
}
impl Operation<Message> for FocusThenReveal {
    fn container(
        &mut self,
        id: Option<&widget::Id>,
        bounds: Rectangle,
        children: &mut dyn FnMut(&mut dyn Operation<Message>),
    ) {
        self.inner.container(id, bounds, children);
    }
    fn focusable(&mut self, state: &mut dyn widget::operation::Focusable, id: Option<&widget::Id>) {
        self.inner.focusable(state, id);
    }
    fn finish(&self) -> widget::operation::Outcome<Message> {
        match self.inner.finish() {
            widget::operation::Outcome::Chain(inner) => {
                widget::operation::Outcome::Chain(Box::new(Self { inner }))
            }
            _ => widget::operation::Outcome::Chain(Box::new(Reveal::default())),
        }
    }
}
pub fn focus(id: widget::Id) -> iced::Task<Message> {
    advanced::widget::operate(FocusThenReveal {
        inner: Box::new(widget::operation::focusable::focus(id)),
    })
}
pub fn move_focus(backwards: bool) -> iced::Task<Message> {
    let inner: Box<dyn Operation<Message>> = if backwards {
        Box::new(widget::operation::focusable::focus_previous())
    } else {
        Box::new(widget::operation::focusable::focus_next())
    };
    advanced::widget::operate(FocusThenReveal { inner })
}
#[derive(Default)]
struct Reveal {
    pending: Option<(widget::Id, Rectangle, Vector)>,
    scroll: Option<(widget::Id, Rectangle, Vector)>,
    offset: Option<(widget::Id, widget::operation::scrollable::AbsoluteOffset)>,
}
impl Operation<Message> for Reveal {
    fn container(
        &mut self,
        id: Option<&widget::Id>,
        bounds: Rectangle,
        children: &mut dyn FnMut(&mut dyn Operation<Message>),
    ) {
        if self
            .pending
            .as_ref()
            .is_some_and(|(key, area, _)| Some(key) == id && *area == bounds)
        {
            let old = self.scroll.take();
            self.scroll = self.pending.take();
            children(self);
            self.scroll = old;
        } else {
            children(self);
        }
    }
    fn scrollable(
        &mut self,
        _: &mut dyn widget::operation::Scrollable,
        id: Option<&widget::Id>,
        bounds: Rectangle,
        _: Rectangle,
        translation: Vector,
    ) {
        self.pending = id.map(|id| (id.clone(), bounds, translation));
    }
    fn custom(&mut self, value: &mut dyn std::any::Any, _: Option<&widget::Id>) {
        let Some(location) = value
            .downcast_ref::<FocusLocation>()
            .filter(|item| item.focused)
        else {
            return;
        };
        let Some((id, viewport, translation)) = &self.scroll else {
            return;
        };
        let area = location.bounds - *translation;
        let delta = |start: f32, size: f32, view_start: f32, view_size: f32| {
            if start < view_start + 4.0 || size + 8.0 > view_size {
                start - view_start - 4.0
            } else if start + size > view_start + view_size - 4.0 {
                start + size - view_start - view_size + 4.0
            } else {
                0.0
            }
        };
        let dx = delta(area.x, area.width, viewport.x, viewport.width);
        let dy = delta(area.y, area.height, viewport.y, viewport.height);
        if dx != 0.0 || dy != 0.0 {
            self.offset = Some((
                id.clone(),
                widget::operation::scrollable::AbsoluteOffset {
                    x: (translation.x + dx).max(0.0),
                    y: (translation.y + dy).max(0.0),
                },
            ));
        }
    }
    fn finish(&self) -> widget::operation::Outcome<Message> {
        match &self.offset {
            Some((id, offset)) => widget::operation::Outcome::Chain(Box::new(
                widget::operation::scrollable::scroll_to(id.clone(), *offset),
            )),
            None => widget::operation::Outcome::None,
        }
    }
}
struct Surface<'a>(Element<'a, Message>);
impl<'a> Widget<Message, Theme, Renderer> for Surface<'a> {
    fn children(&self) -> Vec<Tree> {
        vec![Tree::new(&self.0)]
    }
    fn diff(&self, tree: &mut Tree) {
        tree.diff_children(std::slice::from_ref(&self.0));
    }
    fn size(&self) -> Size<Length> {
        self.0.as_widget().size()
    }
    fn layout(
        &self,
        tree: &mut Tree,
        renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let node = self
            .0
            .as_widget()
            .layout(&mut tree.children[0], renderer, limits);
        accessibility::surface(node.bounds());
        self.0.as_widget().operate(
            &mut tree.children[0],
            Layout::new(&node),
            renderer,
            &mut accessibility::Geometry::new(node.bounds()),
        );
        node
    }
    fn operate(
        &self,
        tree: &mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        operation: &mut dyn Operation,
    ) {
        self.0
            .as_widget()
            .operate(&mut tree.children[0], layout, renderer, operation);
        self.0.as_widget().operate(
            &mut tree.children[0],
            layout,
            renderer,
            &mut accessibility::Geometry::new(layout.bounds()),
        );
    }
    fn on_event(
        &mut self,
        tree: &mut Tree,
        event: Event,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        renderer: &Renderer,
        clipboard: &mut dyn Clipboard,
        shell: &mut Shell<'_, Message>,
        viewport: &Rectangle,
    ) -> event::Status {
        let status = self.0.as_widget_mut().on_event(
            &mut tree.children[0],
            event,
            layout,
            cursor,
            renderer,
            clipboard,
            shell,
            viewport,
        );
        self.0.as_widget().operate(
            &mut tree.children[0],
            layout,
            renderer,
            &mut accessibility::Geometry::new(layout.bounds()),
        );
        status
    }
    fn draw(
        &self,
        tree: &Tree,
        renderer: &mut Renderer,
        theme: &Theme,
        style: &renderer::Style,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
    ) {
        self.0.as_widget().draw(
            &tree.children[0],
            renderer,
            theme,
            style,
            layout,
            cursor,
            viewport,
        );
        accessibility::publish();
    }
    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        viewport: &Rectangle,
        renderer: &Renderer,
    ) -> mouse::Interaction {
        self.0
            .as_widget()
            .mouse_interaction(&tree.children[0], layout, cursor, viewport, renderer)
    }
    fn overlay<'b>(
        &'b mut self,
        tree: &'b mut Tree,
        layout: Layout<'_>,
        renderer: &Renderer,
        translation: Vector,
    ) -> Option<overlay::Element<'b, Message, Theme, Renderer>> {
        self.0
            .as_widget_mut()
            .overlay(&mut tree.children[0], layout, renderer, translation)
    }
}
pub fn surface<'a>(element: Element<'a, Message>) -> Element<'a, Message> {
    Element::new(Surface(element))
}

//! 3-Way SVG Icon Segmented Control for permission grants (Deny / Prompt / Allow).

use crate::{
    app::Message,
    components::icons::{render_icon, IconType},
    controls::custom_button,
    i18n::{tr, Language, TextKey},
    theme::styles::{grant_segment_button, segment_group_container},
};
use iced::{
    widget::{container, row},
    Element, Padding,
};
use racp_contract::local_ipc::Grant;

/// Renders a 3-way SVG icon segmented button group for a given permission item.
pub fn grant_selector<'a>(
    item_id: &'a str,
    current_grant: Option<Grant>,
    lang: Language,
    can_control: bool,
) -> Element<'a, Message> {
    let btn_deny = custom_button(
        render_icon(IconType::Deny, 16.0),
        format!("{} - {}", item_id, tr(lang, TextKey::TooltipDeny)),
    )
    .padding(Padding::from([4, 6]))
    .style(grant_segment_button(current_grant, Grant::Deny))
    .on_press_maybe(can_control.then(|| Message::EditGrant(item_id.to_string(), Grant::Deny)));

    let btn_prompt = custom_button(
        render_icon(IconType::Prompt, 16.0),
        format!("{} - {}", item_id, tr(lang, TextKey::TooltipPrompt)),
    )
    .padding(Padding::from([4, 6]))
    .style(grant_segment_button(current_grant, Grant::RequireApproval))
    .on_press_maybe(can_control.then(|| Message::EditGrant(item_id.to_string(), Grant::RequireApproval)));

    let btn_allow = custom_button(
        render_icon(IconType::Allow, 16.0),
        format!("{} - {}", item_id, tr(lang, TextKey::TooltipAllow)),
    )
    .padding(Padding::from([4, 6]))
    .style(grant_segment_button(current_grant, Grant::Allow))
    .on_press_maybe(can_control.then(|| Message::EditGrant(item_id.to_string(), Grant::Allow)));

    let segment_row = row![btn_deny, btn_prompt, btn_allow].spacing(2);

    container(segment_row)
        .style(segment_group_container)
        .padding(Padding::from(3))
        .into()
}

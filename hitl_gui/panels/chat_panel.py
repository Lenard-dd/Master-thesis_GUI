"""Static Agent chat panel."""

from nicegui import ui


def create_chat_panel(state) -> None:
    with ui.card().classes("w-full h-full min-h-[520px]"):
        ui.label("Agent Chat").classes("text-lg font-semibold")
        messages = ui.column().classes("w-full flex-grow overflow-auto max-h-[390px]")

        def render_messages() -> None:
            messages.clear()
            with messages:
                if not state.messages:
                    ui.label("No messages yet. Send a task to preview the chat interface.").classes("text-grey")
                for entry in state.messages:
                    ui.chat_message(entry.text, name=entry.name, sent=entry.sent)

        task_input = ui.input(placeholder="Enter a robot task (display only)").classes("w-full")

        def send_message() -> None:
            text = task_input.value.strip()
            if not text:
                ui.notify("Please enter a task message.", type="warning")
                return
            state.add_user_message(text)
            task_input.value = ""
            render_messages()

        def clear_messages() -> None:
            state.clear_messages()
            render_messages()

        with ui.row().classes("w-full gap-2"):
            ui.button("Send", on_click=send_message, color="primary")
            ui.button("Clear", on_click=clear_messages).props("outline")
            ui.button("Stop Task", on_click=state.stop_task, color="negative").props("outline")
        task_input.on("keydown.enter", send_message)
        render_messages()

from nicegui import ui


def create_chat_panel(controller):
    with ui.card().classes("w-full h-full min-h-[520px]"):
        ui.label("Agent Chat").classes("text-lg font-semibold")

        @ui.refreshable
        def messages_view():
            with ui.column().classes("w-full flex-grow overflow-auto max-h-[390px]"):
                if not controller.state.conversation:
                    ui.label("No messages yet. Submit a task to start the mock workflow.").classes("text-grey")
                for entry in controller.state.conversation:
                    ui.chat_message(entry.text, name=entry.name, sent=entry.sent)

        messages_view()
        task_input = ui.input(placeholder="Enter a robot task").classes("w-full")

        def send_message():
            if controller.start_task(task_input.value) is None:
                ui.notify("Enter a task, or wait for the current task to finish.", type="warning")
                return
            task_input.value = ""

        with ui.row().classes("w-full gap-2"):
            ui.button("Send", on_click=send_message, color="primary")
            ui.button("Clear", on_click=controller.clear_conversation).props("outline")
            ui.button("Stop Task", on_click=controller.cancel_task, color="negative").props("outline")
        task_input.on("keydown.enter", send_message)
    return messages_view

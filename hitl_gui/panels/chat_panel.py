from nicegui import ui


def create_chat_panel(controller):
    with ui.card().classes("w-full min-h-[460px]"):
        ui.label("Chat").classes("text-lg font-semibold")

        # Keep the actual scroll container outside the refreshable region.
        # Re-creating the container on every UI refresh resets its position to
        # the top, which makes it impossible to read the newest messages.
        rendered = {"message_count": -1, "scroll_requested": False}
        with ui.scroll_area().classes("w-full flex-grow h-[330px]") as message_scroll:

            @ui.refreshable
            def messages_view():
                message_count = len(controller.state.conversation)
                with ui.column().classes("w-full p-2"):
                    if not controller.state.conversation:
                        ui.label("No messages yet. Submit a task to start the mock workflow.").classes("text-grey")
                    for entry in controller.state.conversation:
                        ui.chat_message(entry.text, name=entry.name, sent=entry.sent)

                # Never create timers from inside a refreshable function: this
                # function may be requested by an async Agent task. A stable,
                # page-owned timer below consumes this flag instead.
                if rendered["message_count"] != message_count:
                    rendered["message_count"] = message_count
                    rendered["scroll_requested"] = True

            messages_view()

        def flush_scroll() -> None:
            if rendered["scroll_requested"]:
                rendered["scroll_requested"] = False
                message_scroll.scroll_to(percent=1.0)

        ui.timer(0.1, flush_scroll)
        task_input = ui.input(placeholder="Enter a robot task").classes("w-full")

        def send_message():
            if controller.start_task(task_input.value) is None:
                ui.notify("Enter a task, or wait for the current task to finish.", type="warning")
                return
            task_input.value = ""

        with ui.row().classes("w-full gap-2"):
            send_button = ui.button("Send", on_click=send_message, color="primary")
            ui.button("Clear", on_click=controller.clear_conversation).props("outline")
            ui.button("Stop Task", on_click=controller.cancel_task, color="negative").props("outline")
        task_input.on("keydown.enter", send_message)
    def refresh() -> None:
        messages_view.refresh()
        send_button.set_enabled(not controller.state.agent_request_running)

    return refresh

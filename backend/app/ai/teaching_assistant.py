from __future__ import annotations

from typing import Literal

import dspy


# --- Signatures ---


class RouteQuery(dspy.Signature):
    """Determine the necessary action for a student's query."""
    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    route: Literal["answer", "retrieve", "clarify"] = dspy.OutputField(
        desc="'answer' if the query can be answered from general knowledge/history/course context; 'retrieve' if answering requires specific details from lecture transcripts; 'clarify' if the user query is ambiguous."
    )


class AskClarification(dspy.Signature):
    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    clarification: str = dspy.OutputField(desc="A polite, direct question asking the student to clarify their request.")


class AnswerWithoutContext(dspy.Signature):
    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    answer: str = dspy.OutputField(desc="A direct, helpful answer to the student's question.")


class GenerateRetrievalDetails(dspy.Signature):
    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    contextualized_query: str = dspy.OutputField(
        desc="A self-contained rewrite of the user_query that includes any context needed to understand it standalone."
    )
    lecture_routing: list[str] = dspy.OutputField(
        desc="A list of lecture_ids that may contain information relevant to the user's query. Usually not more than one."
    )
    lecture_and_timestamp: str = dspy.OutputField(
        desc="If the user asked about a specific timestamp, extract it in this format:    . Otherwise return an empty string."
    )


class AnswerFromContext(dspy.Signature):
    """Answer the student's query based on retrieved course documents."""
    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()
    retrieved_docs: list[str] = dspy.InputField(desc="Relevant snippets, timestamps, or full transcripts from the lectures.")

    answer: str = dspy.OutputField(
        desc="Respond to the student with a direct answer (including appropriate citations and formatting). If you don't have enough information to answer with confidence, be honest and state what information is missing."
    )


# --- Module ---


class TeachingAssistant(dspy.Module):
    def __init__(self):
        super().__init__()
        self.router = dspy.ChainOfThought(RouteQuery)
        self.query_generator = dspy.ChainOfThought(GenerateRetrievalDetails)
        self.clarifier = dspy.Predict(AskClarification)
        self.answer_without_context = dspy.ChainOfThought(AnswerWithoutContext)
        self.answer_from_context = dspy.ChainOfThought(AnswerFromContext)

    def forward(self, course_info, conversation_history, user_query):
        # Step 1: Route the query
        route_decision = self.router(
            course_info=course_info,
            conversation_history=conversation_history,
            user_query=user_query
        ).route

        if route_decision == "clarify":
            return self.clarifier(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query
            ).clarification

        if route_decision == "answer":
            return self.answer_without_context(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query
            ).answer

        if route_decision == "retrieve":
            # Step 2: Generate search params
            search_params = self.query_generator(
                conversation_history=conversation_history,
                user_query=user_query,
                course_info=course_info
            )

            # Step 3: Conditional Retrieval Logic
            # Note: Ensure functions like `retrieve_explicitly`, `get_lecture_text`, and
            # `perform_hybrid_search` are defined in your broader application scope.

            retrieved_docs = ""

            if search_params.lecture_and_timestamp:
                retrieved_docs = retrieve_explicitly(search_params.lecture_and_timestamp)

            # If explicit retrieval failed or no timestamp was provided, fall back to routing logic
            if not retrieved_docs:
                lecture_ids = search_params.lecture_routing
                lecture_text, is_long = get_lecture_text(lecture_ids)

                if is_long:
                    # Perform hybrid search -> chunked docs
                    retrieved_docs = perform_hybrid_search(search_params.contextualized_query, lecture_ids)
                else:
                    # Bypass chunking: entire lecture transcripts are the context
                    retrieved_docs = lecture_texts

            # Step 4: Final Generation
            return self.answer_from_context(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query,
                retrieved_docs=str(retrieved_docs)
            ).answer

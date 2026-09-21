from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class FigureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figure_number: StrictInt = Field(ge=1)
    filename: StrictStr = Field(min_length=1)
    file_path: StrictStr = ""
    description: StrictStr = ""
    analysis: StrictStr = ""


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["generate_code", "collect_figures", "analysis_complete"]
    code: StrictStr | None = None
    figures_to_collect: list[FigureRequest] = Field(default_factory=list)
    final_report: StrictStr | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "AgentAction":
        if self.action == "generate_code" and not (self.code and self.code.strip()):
            raise ValueError("generate_code requires nonblank code")
        if self.action == "analysis_complete" and not (
            self.final_report and self.final_report.strip()
        ):
            raise ValueError("analysis_complete requires nonblank final_report")
        return self


__all__ = ["AgentAction", "FigureRequest"]

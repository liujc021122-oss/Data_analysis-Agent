from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class PaginationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: StrictInt = Field(default=1, ge=1)
    page_size: StrictInt = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


T = TypeVar("T")


class PageResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    items: list[T] | tuple[T, ...]
    page: StrictInt = Field(ge=1)
    page_size: StrictInt = Field(ge=1, le=100)
    total: StrictInt = Field(ge=0)
    has_next: bool


Page = PageResponse

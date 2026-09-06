package core

import "liveaio/resources"

// Gift catalog lives in package resources; core keeps thin aliases for hub callers.

type Gift = resources.Gift

func GiftDir(root string) string                 { return resources.GiftDir(root) }
func LoadGifts(root string) error                { return resources.LoadGifts(root) }
func ClearGiftCatalog()                          { resources.ClearCatalog() }
func GiftCatalogLoaded() bool                    { return resources.CatalogLoaded() }
func Diamonds(name string) int                   { return resources.Diamonds(name) }
func GiftID(name string) int                     { return resources.GiftID(name) }
func Names() []string                            { return resources.Names() }
func IconPath(root, name string) string          { return resources.IconPath(root, name) }
